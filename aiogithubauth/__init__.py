from aioauth_client import GithubClient
import os
from aiohttp_session import get_session, session_middleware
from aiohttp_session.cookie_storage import EncryptedCookieStorage
from aiohttp import web
import urllib

gh_id = None
gh_secret = None
gh_org = None


def github_auth_middleware(*, github_id, github_secret, github_org,
                           whitelist_handlers=None, api_unauthorized=False):
    """ Middleware for github authentication
    :param github_id: github client id
    :param github_secret: github secret
    :param github_org: github organization for which people are authorized
    :param whitelist_handlers: a list of handler methods
        which do not need authorization
    :param api_unauthorized: if set to True, any call without authorization
        made at a path of /api/* will return a 401 instead of
        redirecting to github login

    :return: middleware_factory
    """
    global gh_id, gh_secret, gh_org
    gh_id = github_id
    gh_secret = github_secret
    gh_org = github_org
    whitelist_handlers = whitelist_handlers or []

    async def middleware_factory(app, handler):

        async def auth_handler(request):
            session = await get_session(request)
            params = urllib.parse.parse_qs(request.query_string)
            user = session.get('User')
            if user:  # Already authenticated
                request['user'] = user
            elif handler in whitelist_handlers:  # We don't need authentication
                pass
            elif handler == handle_github_callback and \
                    session.get('github_state'):
                # Attempting to authenticate - let them pass through
                        pass

            elif api_unauthorized and request.path.startswith('/api/'):
                return web.HTTPUnauthorized()
            else:
                gh = GithubClient(
                    client_id=gh_id,
                    client_secret=gh_secret
                )
                state = os.urandom(30).hex()
                authorize_url = gh.get_authorize_url(
                    scope='user:email read:org',
                    state=state)
                session['github_state'] = state
                session['desired_location'] = request.path
                return web.HTTPFound(authorize_url)

            return await handler(request)

        return auth_handler

    return middleware_factory


async def handle_github_callback(request):
    params = urllib.parse.parse_qs(request.query_string)
    session = await get_session(request)

    # Check conditions
    if (session.get('github_state') !=  # Github_state is incorrect
                params.get('state', [None])[0]):
        print('bad state returned')
        """
        Codes are the same, we are in the middle of
        authenticating and things look ok, carry on
        """
        return web.HTTPForbidden()

    gh = GithubClient(
        client_id=gh_id,
        client_secret=gh_secret
    )
    code = params.get('code', [None])[0]
    if not code:
        return web.HTTPNotFound(body=b'Page not found. Its possible the '
                                     b'session timed out while authenticating.')
    otoken, _ = await gh.get_access_token(code)
    gh = GithubClient(
        client_id=gh_id,
        client_secret=gh_secret,
        access_token=otoken
    )
    req = await gh.request('GET', 'user')
    user = await req.json()
    req.close()
    req = await gh.request('GET', 'user/orgs')
    orgs = await req.json()
    req.close()

    for org in orgs:
        if org.get('login') == gh_org:

            # Swap github_state for user
            session.pop('github_state', None)
            session['User'] = user.get('login')
            location = session.pop('desired_location')
            return web.HTTPFound(location)

    return web.HTTPForbidden()


def add_github_auth_middleware(app,
                               cookie_key=None,
                               cookie_name='aiogithubauth',
                               **kwargs):
    if cookie_key is None:
        print('creating new cookie secret')
        cookie_key = os.urandom(16).hex()

    app._middlewares = app._middlewares + [
        session_middleware(
            EncryptedCookieStorage(cookie_key.encode(),
                                   cookie_name=cookie_name,
                                   max_age=7200)),  # two hours
        github_auth_middleware(**kwargs)
    ]

    app.router.add_route('GET', '/oauth_callback/github',
                         handle_github_callback)

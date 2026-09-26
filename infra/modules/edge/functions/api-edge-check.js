// CloudFront Function (cloudfront-js-2.0), viewer request, behavior /api/*.
// Rejects API calls without a session cookie before they reach Lambda, so junk traffic
// costs nothing. The health check and the sign-in routes stay public.
var PUBLIC_PATHS = ['/api/health'];
var PUBLIC_PREFIXES = ['/api/auth/'];
var SESSION_COOKIE = '__Host-session';

function isPublic(uri) {
  if (PUBLIC_PATHS.indexOf(uri) !== -1) {
    return true;
  }
  for (var i = 0; i < PUBLIC_PREFIXES.length; i++) {
    if (uri.indexOf(PUBLIC_PREFIXES[i]) === 0) {
      return true;
    }
  }
  return false;
}

function handler(event) {
  var request = event.request;
  if (isPublic(request.uri)) {
    return request;
  }
  var cookie = request.cookies && request.cookies[SESSION_COOKIE];
  if (cookie && cookie.value) {
    return request;
  }
  return {
    statusCode: 401,
    statusDescription: 'Unauthorized',
    headers: {
      'content-type': { value: 'application/problem+json' },
      'cache-control': { value: 'no-store' }
    }
  };
}

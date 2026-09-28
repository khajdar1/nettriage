// CloudFront Function (cloudfront-js-2.0), viewer request, default (S3) behavior.
// Serves index.html for client-side routes: paths whose last segment has no file extension.
// API paths never reach this behavior; the guard keeps them untouched regardless.
function handler(event) {
  var request = event.request;
  var uri = request.uri;
  if (uri.indexOf('/api/') === 0) {
    return request;
  }
  var lastSegment = uri.substring(uri.lastIndexOf('/') + 1);
  if (lastSegment.indexOf('.') === -1) {
    request.uri = '/index.html';
  }
  return request;
}

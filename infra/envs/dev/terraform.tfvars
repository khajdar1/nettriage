# Non-secret dev settings, reviewed like code. `just preflight` checks both layer ARNs exist in
# eu-north-1 and support arm64; if one is missing, use the current version from the layer's
# release notes (Lambda Web Adapter README; opentelemetry-lambda "layer-collector" releases).
lwa_layer_arn            = "arn:aws:lambda:eu-north-1:753240598075:layer:LambdaAdapterLayerArm64:30"
otel_collector_layer_arn = "arn:aws:lambda:eu-north-1:184161586896:layer:opentelemetry-collector-arm64-0_22_0:1"
grafana_otlp_endpoint    = "<your Grafana Cloud OTLP endpoint, e.g. https://otlp-gateway-prod-eu-north-0.grafana.net/otlp>"

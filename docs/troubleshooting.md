# Troubleshooting

| Symptom | Likely cause | Fix |
| ------- | ------------ | --- |
| `unauthorized` on every call | `VISION_API_KEYS` unset or wrong key | Set the key and send `Authorization: Bearer <key>` or `X-API-Key`. |
| Startup fails in production | Auth disabled or no API keys configured | Production fails closed by design; configure both. |
| `forbidden` for a local path | Path outside `VISION_ALLOWED_ROOTS`, a symlink escaping the root, or not a regular file | Move the file under an allowed root. |
| `unsupported_media` | Not PNG, JPEG, or WebP by magic bytes | Convert the image first. |
| `payload_too_large` | Body above `VISION_MAX_JSON_BYTES` or image above the byte or pixel limit | Upload as an asset or shrink the image. |
| `provider_unavailable` in local mode | `paddleocr` not installed | Install the `ml` extra or build the image with `--build-arg EXTRAS=ml,azure`. |
| `provider_unavailable` in azure mode | Endpoint unset or unreachable, or missing role assignment | Check the endpoint and the Cognitive Services User assignment for the workload identity. |
| `busy` responses | Queue depth exceeded | Retry with backoff or raise `VISION_MAX_CONCURRENCY`/replica count. |
| `timeout` | Operation or provider timeout exceeded | Reduce image size or raise the timeout settings. |
| `not_found` for an asset | Expired, deleted, or created by a different principal | Re-upload; asset IDs are principal-scoped. |
| First hosted request is slow | Scale-to-zero cold start, plus model load when the local provider is enabled | Set `minReplicas` to 1 for latency-sensitive use. |

## Deferred capabilities

The following are deliberately not implemented in this phase and no tool claims them: UI mapping,
diagram parsing, object detection, visual question answering, and text-region grounding. The
deprecated Azure Image Analysis 4.0 API is not used, and the Azure ML provider is a documented
extension point only.

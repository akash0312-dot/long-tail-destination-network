# Log audit: PASSED

Source: `compose.log`, 756 JSON log events, 169 platform calls.

## 1. Where requests went

Hosts contacted: `ghost:2368`, `lemmy:8536`, `mock:9000`. Allowed: our own local instances and mock servers only.

## 2. Certification test posts deleted

22 test posts created, 22 deleted; slowest deletion 2.46 s (limit 600 s).

| Run | Connector | Host | Deleted | Seconds to delete |
|---|---|---|---|---|
| 261a4adf | devto | mock:9000 | yes | 0.06 |
| 3c487c40 | webhook | mock:9000 | yes | 0.04 |
| 3c6e43b3 | discourse | mock:9000 | yes | 0.07 |
| 64600cc7 | lemmy | lemmy:8536 | yes | 0.26 |
| 678f13c2 | ghost | ghost:2368 | yes | 2.46 |
| 74c068db | discourse | mock:9000 | yes | 0.14 |
| 759938bb | devto | mock:9000 | yes | 0.06 |
| 7f32f652 | discourse | mock:9000 | yes | 0.07 |
| 8b180fa6 | hashnode | mock:9000 | yes | 0.05 |
| 8d7ded10 | devto | mock:9000 | yes | 0.07 |
| 9843d852 | devto | mock:9000 | yes | 0.07 |
| 9ced53df | devto | mock:9000 | yes | 0.08 |
| aa7f2d80 | peertube | mock:9000 | yes | 0.1 |
| b7a2d3bf | devto | mock:9000 | yes | 0.08 |
| bbadfa2a | devto | mock:9000 | yes | 0.06 |
| bd0ab9d4 | devto | mock:9000 | yes | 0.07 |
| e363d9f4 | discourse | mock:9000 | yes | 0.1 |
| e4864a5c | webhook | mock:9000 | yes | 0.11 |
| eea7ec30 | discourse | mock:9000 | yes | 0.08 |
| efea29bb | discourse | mock:9000 | yes | 0.12 |
| f277024d | discourse | mock:9000 | yes | 0.07 |
| fbdf4680 | discourse | mock:9000 | yes | 0.09 |

## 3. Rate limits (back off on 429 using the reset time)

1 inline backoffs; 2 job(s) returned PLATFORM_RATE_LIMIT with the retry time (long resets are rescheduled, not hammered).

| Connector | Op | Waited (s) | Reset time from |
|---|---|---|---|
| devto | publish | 2.0 | Retry-After |

Circuit breaker openings (calls stopped after 5 consecutive failures): 0.

## 4. Secrets

jwt: 0, bearer token: 0, ghost admin token: 0, local default password: 0. Values redacted in the logs: 0.

## Violations

None.

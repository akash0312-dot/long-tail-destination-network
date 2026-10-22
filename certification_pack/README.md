# Certification pack

Every certified destination: the run record (`run.json`, every step with its result), the live response captured at certification time (`live_snapshot.*`), a screenshot of it (`screenshot.png`), and the deletion confirmation (the `delete_test_post` and `status_deleted` steps). Exported by `scripts/export_certification_pack.py` from http://localhost:8000. Screenshots: 9 rendered with Chromium (headless) from the captured responses. They render the response exactly as captured, offline, so stylesheets served from internal hosts are not loaded (pages look unstyled); ActivityPub and other JSON responses are shown as formatted text.

| Destination | Connector | Level | Test post | Live check | Deleted | Status after delete | Cleanup | Run time |
|---|---|---|---|---|---|---|---|---|
| [Show-and-tell board (assisted, a person posts)](assisted--show-and-tell-board-assisted-a-person-posts/) | assisted | L1 | n/a | n/a | n/a (person posts) | n/a | n/a | 0.06 s |
| [Dev community (local mock Forem)](devto--dev-community-local-mock-forem/) | devto | L4 | 3 | HTTP 200 | yes | deleted | 0.2 s | 0.18 s |
| [Creator Ops Forum (local mock Discourse)](discourse--creator-ops-forum-local-mock-discourse/) | discourse | L4 | 1 | HTTP 200 | yes | deleted | 0.1 s | 0.14 s |
| [Long-Tail Test Blog (local Ghost)](ghost--long-tail-test-blog-local-ghost/) | ghost | L3 | 6ada5ee08ba8bb00019d9e52 | HTTP 200 | yes | deleted | 2.1 s | 2.09 s |
| [Builders blog (local mock Hashnode)](hashnode--builders-blog-local-mock-hashnode/) | hashnode | L3 | 000000000000000000000005 | HTTP 200 | yes | deleted | 0.1 s | 0.13 s |
| [!longtail_test (local Lemmy)](lemmy--longtail-test-local-lemmy/) | lemmy | L4 | 2 | HTTP 200 | yes | deleted | 1.2 s | 1.23 s |
| [Creator video feed (hosted MRSS)](mrss--creator-video-feed-hosted-mrss/) | mrss | L2 | ltn-5848caf2f3a95d660b57 | HTTP 200 | yes | deleted | 0.2 s | 0.16 s |
| [Creator channel (local mock PeerTube)](peertube--creator-channel-local-mock-peertube/) | peertube | L4 | 6 | HTTP 200 | yes | deleted | 0.2 s | 0.18 s |
| [Partner site demo (local mock partner)](webhook--partner-site-demo-local-mock-partner/) | webhook | L3 | 4 | HTTP 200 | yes | deleted | 0.1 s | 0.1 s |

Levels: L1 assisted (a person posts), L2 feed, L3 native API (publish, status, delete tested), L4 native + metrics. Ghost and Lemmy run as real local instances; the others run on the mock platform server (see REPORT.md).

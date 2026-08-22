# v4.8.0 Performance Core

Deploy all services from the same commit. The runtime stream namespace changes to `perf480`, so old Page/Date/View queue state is not consumed. PostgreSQL and AI history are untouched.

Recommended smoke test:
1. wait for Date/Page/View 4/4;
2. run one 50-page scan;
3. compare Date, Page, Views durations;
4. run two scans concurrently;
5. inspect worker admin screens for `traffic wait` and `Redis limiter wait`.

# k6 Results

This folder stores k6 JSON output and metadata captured by helper scripts.

## Usage

```bash
RUN_LABEL="baseline" ./k6/run_capture.sh ./k6/run_mix.sh
```

## Summaries

```bash
python ./k6/summarize_k6.py ./k6/results/<timestamp>-<label>.json
```

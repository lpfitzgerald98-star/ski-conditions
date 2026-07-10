FROM python:3.12-slim

# libgomp is a runtime dep of numpy/pandas wheels; without it `import pandas`
# dies at container start with a bare "libgomp.so.1: cannot open shared object".
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so a code edit doesn't reinstall pandas on every build.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The 90MB observation DB is gitignored but IS baked into the image, as a seed.
# The entrypoint copies it onto the persistent volume only if the volume is empty,
# so a redeploy never overwrites live data with the snapshot from build time.
RUN mkdir -p /app/seed && cp data/ski.db /app/seed/ski.db 2>/dev/null || true

ENV SKI_DB_PATH=/data/ski.db \
    PYTHONUNBUFFERED=1

EXPOSE 8080
ENTRYPOINT ["/app/docker-entrypoint.sh"]

# ONE worker, deliberately. Two would each open the same SQLite file and each
# spawn their own live-stream thread pool; the scoring work is CPU-bound and holds
# the GIL, so a second worker buys contention, not throughput. Scale by making the
# machine bigger, not by adding workers.
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]

# Lambda container image for the thumbnail worker.
FROM ghcr.io/astral-sh/uv:0.12.23 AS uv

FROM public.ecr.aws/lambda/python:3.13 AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /build
COPY pyproject.toml uv.lock ./
# Only the runtime dependencies from the lock file (no Pulumi, no test tools).
RUN uv export --frozen --no-default-groups --no-emit-project --no-hashes -o requirements.txt \
 && uv pip install --no-cache --target /deps -r requirements.txt

FROM public.ecr.aws/lambda/python:3.13
COPY --from=build /deps ${LAMBDA_TASK_ROOT}
COPY src/thumbnailer ${LAMBDA_TASK_ROOT}/thumbnailer
CMD ["thumbnailer.handler.handler"]

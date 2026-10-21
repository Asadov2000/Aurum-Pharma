# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e

ARG GO_IMAGE=golang:1.26.10-alpine@sha256:9ce231b28998b32e116927c61aa5c406183061d4f3f2d61b292c88e7f6bdf7ca

FROM ${GO_IMAGE} AS tools-builder

# Avoid HTTP/2 transport failures while retaining HTTPS and checksum verification.
ENV GODEBUG=http2client=0

ARG TARGETARCH

RUN apk add --no-cache git

WORKDIR /src/restic
RUN --mount=type=cache,id=aurum-go-mod-${TARGETARCH},target=/go/pkg/mod,sharing=locked \
    --mount=type=cache,id=aurum-go-build-${TARGETARCH},target=/root/.cache/go-build,sharing=locked \
    go mod init aurum.local/restic \
    && go get github.com/restic/restic/cmd/restic@v0.19.1 \
    && go get golang.org/x/net@v0.60.0 \
        golang.org/x/text@v0.42.0 \
        golang.org/x/crypto@v0.57.0 \
        google.golang.org/grpc@v1.83.2 \
        github.com/klauspost/compress@v1.18.7 \
    && go list -m golang.org/x/net golang.org/x/text golang.org/x/crypto github.com/klauspost/compress \
    && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" \
        -o /out/restic github.com/restic/restic/cmd/restic \
    && go version -m /out/restic | awk '$1 == "dep" && $2 == "github.com/klauspost/compress" { print $2, $3; ok = ($3 == "v1.18.7") } END { exit !ok }'

WORKDIR /src/minio-client
RUN --mount=type=cache,id=aurum-go-mod-${TARGETARCH},target=/go/pkg/mod,sharing=locked \
    --mount=type=cache,id=aurum-go-build-${TARGETARCH},target=/root/.cache/go-build,sharing=locked \
    go mod init aurum.local/minio-client \
    && go get github.com/minio/mc@7394ce0dd2a80935aded936b09fa12cbb3cb8096 \
    && go get github.com/prometheus/prometheus@v0.311.3 \
    && go get github.com/prometheus/common/model@v0.67.5 \
        github.com/prometheus/prometheus/model/labels@v0.311.3 \
    && go get golang.org/x/net@v0.60.0 \
        golang.org/x/text@v0.42.0 \
        golang.org/x/crypto@v0.57.0 \
        google.golang.org/grpc@v1.83.2 \
        github.com/klauspost/compress@v1.18.7 \
    && go list -m golang.org/x/net golang.org/x/text golang.org/x/crypto github.com/klauspost/compress \
    && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" \
        -o /out/mc github.com/minio/mc \
    && go version -m /out/mc | awk '$1 == "dep" && $2 == "github.com/klauspost/compress" { print $2, $3; ok = ($3 == "v1.18.7") } END { exit !ok }'

FROM postgres:16-alpine@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685 AS runtime

COPY --from=tools-builder /out/mc /usr/local/bin/mc
COPY --from=tools-builder /out/restic /usr/local/bin/restic

RUN apk upgrade --no-cache \
    && apk add --no-cache jq openssl \
    && rm -f /usr/local/bin/gosu /usr/local/bin/docker-entrypoint.sh \
    && addgroup -S -g 10001 aurum \
    && adduser -S -D -H -u 10001 -G aurum aurum \
    && addgroup aurum postgres

COPY --chown=10001:10001 \
    backup.sh offsite-common.sh offsite-sync.sh offsite-trust-verify.sh \
    offsite-trust-sign.sh offsite-trusted-restore.sh pitr-basebackup.sh \
    pitr-restore-drill.sh restore-drill.sh wal-snapshot.sh \
    /opt/aurum/

USER 10001:10001
WORKDIR /workspace

ENTRYPOINT []
CMD ["/opt/aurum/backup.sh"]

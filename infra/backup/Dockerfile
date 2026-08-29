# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e

ARG GO_IMAGE=golang:1.26.6-alpine@sha256:3889b425f035be855a72fb4755265311293b6d414521f0a519d819df32222d83

FROM ${GO_IMAGE} AS tools-builder

RUN apk add --no-cache git

WORKDIR /src/restic
RUN go mod init aurum.local/restic \
    && go get github.com/restic/restic/cmd/restic@v0.19.1 \
    && go get golang.org/x/net@v0.56.0 \
        golang.org/x/text@v0.39.0 \
        google.golang.org/grpc@v1.82.1 \
    && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" \
        -o /out/restic github.com/restic/restic/cmd/restic

WORKDIR /src/minio-client
RUN go mod init aurum.local/minio-client \
    && go get github.com/minio/mc@7394ce0dd2a80935aded936b09fa12cbb3cb8096 \
    && go get github.com/prometheus/prometheus@v0.311.3 \
        golang.org/x/crypto@v0.53.0 \
        golang.org/x/net@v0.56.0 \
        golang.org/x/text@v0.39.0 \
        google.golang.org/grpc@v1.82.1 \
    && go get github.com/prometheus/common/model@v0.67.5 \
        github.com/prometheus/prometheus/model/labels@v0.311.3 \
    && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" \
        -o /out/mc github.com/minio/mc

FROM postgres:16-alpine@sha256:4327b9fd295502f326f44153a1045a7170ddbfffed1c3829798328556cfd09e2

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

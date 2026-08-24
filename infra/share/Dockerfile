# syntax=docker/dockerfile:1.7

ARG CADDY_IMAGE=caddy:2.11.4-alpine@sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648
FROM ${CADDY_IMAGE}

RUN addgroup -S -g 10001 aurum \
    && adduser -S -D -H -u 10001 -G aurum aurum \
    && setcap -r /usr/bin/caddy

USER 10001:10001

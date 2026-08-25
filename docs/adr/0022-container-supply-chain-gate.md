# ADR-0022: Container supply-chain gate

- Статус: принято
- Дата: 2026-08-22

## Контекст

Dependency audit не видит системные пакеты внутри готового image и не создаёт
SBOM. Случайный секрет в commit также должен блокировать merge.

## Решение

1. Base images и Dockerfile frontend syntax закрепляются digest.
2. `docker build --check` проверяет все production Dockerfiles.
3. Trivy фиксированной версии и digest выполняет:
   - secret scan рабочего дерева;
   - HIGH/CRITICAL configuration scan production Dockerfiles;
   - image scan, блокирующий исправимые HIGH и CRITICAL CVE;
   - CycloneDX SBOM для backend, gateway и recovery tooling.
4. SBOM загружается как CI artifact и хранится 30 дней.
5. Эти проверки входят в обязательный `production-containers` job, от которого
   зависит единый `CI gate`.

## Исключения и выпуск

Исключение CVE запрещено добавлять без ссылки на advisory, доказательства
неприменимости, владельца риска и даты удаления исключения. Release images должны
публиковаться по digest и подписываться keyless Cosign после настройки GHCR и
GitHub OIDC; локальный `:ci` image подписывать бессмысленно.

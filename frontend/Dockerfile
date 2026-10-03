FROM node:24-alpine

WORKDIR /app

RUN corepack enable && corepack prepare pnpm@9.12.0 --activate

COPY package.json pnpm-lock.yaml ./
COPY patches ./patches

RUN pnpm install --frozen-lockfile

COPY . .

ARG VITE_API_URL=http://localhost:8000/api/v1
ENV VITE_API_URL=$VITE_API_URL
ARG VITE_PUBLIC_WEBSITE_URL
ENV VITE_PUBLIC_WEBSITE_URL=$VITE_PUBLIC_WEBSITE_URL

RUN pnpm build

EXPOSE 5173

CMD ["pnpm", "dev", "--host", "0.0.0.0"]

FROM node:22-alpine AS build
WORKDIR /frontend
RUN npm install -g pnpm@10
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build
FROM caddy:2-alpine
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /frontend/dist /srv
EXPOSE 80 443

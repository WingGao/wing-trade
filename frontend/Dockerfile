FROM node:22-alpine AS build
ARG NPM_REGISTRY=https://registry.npmmirror.com
ENV COREPACK_NPM_REGISTRY=${NPM_REGISTRY}
ENV npm_config_registry=${NPM_REGISTRY}
WORKDIR /app
COPY package.json pnpm-workspace.yaml ./
RUN corepack enable && pnpm install --no-frozen-lockfile --registry=${NPM_REGISTRY}
COPY . .
RUN pnpm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80

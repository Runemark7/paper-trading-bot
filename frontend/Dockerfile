# syntax=docker/dockerfile:1.7
# Frontend — mirrors swedish-democratic-tracker/frontend/Dockerfile:
# builder (node) -> nginx runtime, non-root, security headers.
FROM node:25-alpine AS builder

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

# Minimal nginx image
FROM nginx:1.27-alpine

# Links this image to the GitHub repo so GHCR can inherit repo pull access.
LABEL org.opencontainers.image.source=https://github.com/Runemark7/paper-trading-bot

RUN rm -f /etc/nginx/conf.d/default.conf /etc/nginx/conf.d/default.conf.template

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/nginx.conf
COPY nginx-server.conf.template /etc/nginx/templates/default.conf.template

ENV BACKEND_URL=http://web:8787
# writeable by nginx for envsubst at startup
RUN chown -R nginx:nginx /usr/share/nginx/html \
  && chown -R nginx:nginx /etc/nginx/conf.d \
  && chown -R nginx:nginx /etc/nginx/templates

USER nginx
EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]

FROM node:22-alpine
WORKDIR /app
COPY app/ .
# Install the app's dependencies, if it has any (the starter has none).
RUN if [ -f package-lock.json ]; then npm ci --omit=dev --no-audit --no-fund; \
    elif [ -f package.json ]; then npm install --omit=dev --no-audit --no-fund; fi
ARG APP_VERSION=dev
ENV PORT=8080 APP_VERSION=$APP_VERSION NODE_ENV=production
# The node image's "node" user, by number so runAsNonRoot can check it.
USER 1000
EXPOSE 8080
CMD ["node", "server.js"]

# Dev-mode Dockerfile for the AgentRadar dashboard.
# Runs Vite's dev server with HMR. Production deployment uses the
# multi-stage build in infra/nginx/Dockerfile (which produces a static bundle).

FROM node:20-alpine

WORKDIR /app

# Install deps in a separate layer so this caches when only source changes.
# Bind mount handles source — we still need package files at build time
# so the layer cache works.
COPY apps/dashboard/package.json apps/dashboard/package-lock.json* ./
RUN npm ci

EXPOSE 5173

# Vite needs --host to bind 0.0.0.0 (default is localhost, which inside
# a container means "the container itself," unreachable from outside).
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
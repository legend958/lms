FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
# Next.js evaluates rewrites while building, so the API host must be present at
# build time. Compose overrides this with http://api:8000.
ARG API_INTERNAL_URL=http://localhost:8000
ENV API_INTERNAL_URL=$API_INTERNAL_URL
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=builder /app/package.json ./package.json
COPY --from=builder /app/package-lock.json ./package-lock.json
COPY --from=builder /app/next.config.ts ./next.config.ts
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/.next ./.next

# Run as the image's unprivileged `node` user. Next.js only needs to write its
# incremental cache at runtime; everything else is read-only.
RUN mkdir -p /app/.next/cache && chown -R node:node /app/.next/cache

USER node
ENV HOME=/home/node

EXPOSE 3000
CMD ["npm", "start"]

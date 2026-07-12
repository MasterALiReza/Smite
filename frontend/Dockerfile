# Stage 1: Dependencies (cached layer)
FROM node:18-alpine AS deps
WORKDIR /app

# Copy package files
COPY package.json package-lock.json* ./

# Install dependencies with cache (use npm ci if lock file exists, otherwise npm install)
RUN --mount=type=cache,target=/root/.npm \
    if [ -f package-lock.json ]; then \
        npm ci --prefer-offline --no-audit --no-fund; \
    else \
        npm install --prefer-offline --no-audit --no-fund; \
    fi

# Stage 2: Build
FROM node:18-alpine AS build
WORKDIR /app

# Copy dependencies from deps stage
COPY --from=deps /app/node_modules ./node_modules
COPY package.json package-lock.json* ./

# Copy source code
COPY . .

# Build application
RUN npm run build

# Stage 3: Production (nginx)
FROM nginx:alpine

# Copy built files from build stage
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]

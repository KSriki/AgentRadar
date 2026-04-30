FROM nginx:1.27-alpine

RUN rm /etc/nginx/conf.d/default.conf
COPY infra/nginx/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD wget -qO- http://localhost/health > /dev/null || exit 1

CMD ["nginx", "-g", "daemon off;"]
# The policy module is built without vendoring or changing ejabberd itself.
# OTP matches the upstream ejabberd 26.09 container build.
FROM docker.io/erlang:28.5.0-alpine AS policy
WORKDIR /build
COPY deploy/ejabberd/mod_sabz_chat.erl ./
RUN mkdir ebin && erlc -o ebin mod_sabz_chat.erl

FROM ghcr.io/processone/ejabberd:26.09
# Upstream marks BEAM with NET_BIND_SERVICE. These listeners use high ports;
# remove the file capability so exec works with cap_drop: ALL and no-new-privileges.
USER root
RUN apk add --no-cache libcap \
    && setcap -r /opt/ejabberd-*/erts-*/bin/beam.smp
USER ejabberd
COPY --from=policy --chown=9000:9000 /build/ebin/ /opt/ejabberd/custom/ebin/
ENV ERL_FLAGS="-pa /opt/ejabberd/custom/ebin"

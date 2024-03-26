#!/bin/bash

# https://github.com/edx/edx-arch-experiments/issues/580
export DD_TRACE_PYMONGO_ENABLED=false

#export DD_TRACE_DEBUG=true

ddtrace-run "$@"

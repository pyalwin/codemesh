#!/bin/bash
export CODEMESH_PROJECT_ROOT="/tmp/alamofire"
claude --print --mcp-config eval/results/codegraph_bench/mcp-config-tmp.json --disallowedTools Grep,Glob --allowedTools Read --allowedTools Bash --allowedTools mcp__codemesh__codemesh_explore "Trace how a request flows from Session.request() through to the URLSession layer"

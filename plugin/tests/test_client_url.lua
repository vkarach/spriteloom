-- Plain-Lua tests for the client URL: run with `lua plugin/tests/test_client_url.lua`
-- from the repo root. The port comes from server.json, otherwise it is 8765.
local failures = 0
local function eq(got, want, what)
  if got ~= want then
    failures = failures + 1
    print(string.format("FAIL %s\n  got:  %s\n  want: %s",
                        what, tostring(got), tostring(want)))
  end
end

_G.app = { fs = { joinPath = function(a, b) return a .. "/" .. b end } }
_G.json = {
  decode = function(text)
    local port = text:match('"port"%s*:%s*(%d+)')
    if not port then error("bad json") end
    return { port = tonumber(port) }
  end,
  encode = function() return "{}" end,
}
_G.WebSocket = function()
  return { connect = function() end, close = function() end,
           sendText = function() end }
end
_G.WebSocketMessageType = { OPEN = 1, TEXT = 2, CLOSE = 3 }

local function loadClient(dir)
  return assert(loadfile("plugin/client.lua"))(dir)
end

local tmp = os.getenv("TEMP") or "/tmp"

-- no folder at all: the built in default holds
eq(loadClient(tmp .. "/spriteloom_no_such_dir").URL,
   "http://127.0.0.1:8765", "missing server.json")

-- nil pluginDir (loaded with dofile by an older caller): still the default
eq(loadClient(nil).URL, "http://127.0.0.1:8765", "no plugin dir")

local dir = tmp .. "/spriteloom_port_test"
os.execute('mkdir "' .. dir:gsub("/", "\\") .. '" 2>nul')

-- a real port in the file wins
local f = assert(io.open(dir .. "/server.json", "w"))
f:write('{ "port": 9100 }')
f:close()
eq(loadClient(dir).URL, "http://127.0.0.1:9100", "port from file")

-- garbage in the file falls back instead of throwing
f = assert(io.open(dir .. "/server.json", "w"))
f:write("{not json")
f:close()
eq(loadClient(dir).URL, "http://127.0.0.1:8765", "broken server.json")

os.remove(dir .. "/server.json")

if failures > 0 then
  print(failures .. " failure(s)")
  os.exit(1)
end
print("test_client_url ok")

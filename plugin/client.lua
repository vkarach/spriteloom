-- WebSocket client for the Spriteloom server. One request at a time.
local pluginDir = ...

-- the launcher writes server.json here; without it the built in default holds
local function readPort()
  if not pluginDir then return 8765 end
  local f = io.open(app.fs.joinPath(pluginDir, "server.json"), "r")
  if not f then return 8765 end
  local text = f:read("a"); f:close()
  local ok, data = pcall(json.decode, text)
  if ok and type(data) == "table" and type(data.port) == "number" then
    return math.floor(data.port)
  end
  return 8765
end

local M = { URL = "http://127.0.0.1:" .. readPort() }

local OFFLINE = "Server offline. Open the Spriteloom app and press Start."

-- a socket whose peer is gone throws from sendText and close, and an
-- uncaught throw here opens the Aseprite Console on top of the panel
local function send(ws, payload)
  return pcall(function() ws:sendText(json.encode(payload)) end)
end

local function shut(ws)
  pcall(function() ws:close() end)
end

-- Aseprite's WebSocket takes an http:// url. json global is built in (1.3+).
-- One-shot socket: connect, send payload, hand each decoded TEXT to
-- onText(msg, finish); a lost connect or a CLOSE reports onFail once. Returns
-- finish so a caller can cancel. (M.ping is persistent, so it stays separate.)
local function oneShot(payload, onText, onFail)
  local ws, timer
  local done = false
  local function finish()
    if not done then
      done = true
      if timer then timer:stop() end
      shut(ws)
    end
  end
  -- hard means the socket is provably gone (send threw, or a CLOSE arrived);
  -- a silent connect is only soft evidence, a loaded server can be slow
  local function fail(msg, hard)
    if not done then finish(); onFail(msg, hard) end
  end
  ws = WebSocket{
    url = M.URL,
    deflate = false,
    onreceive = function(mt, data)
      if mt == WebSocketMessageType.OPEN then
        if timer then timer:stop() end
        if not send(ws, payload) then fail(OFFLINE, true) end
      elseif mt == WebSocketMessageType.TEXT then
        local ok, msg = pcall(json.decode, data)
        if ok then onText(msg, finish) else fail("bad server reply") end
      elseif mt == WebSocketMessageType.CLOSE then
        fail(OFFLINE, true)
      end
    end,
  }
  if Timer then
    timer = Timer{ interval = 5.0, ontick = function() fail(OFFLINE, false) end }
    timer:start()
  end
  ws:connect()
  return finish
end

function M.request(payload, callbacks)
  return { cancel = oneShot(payload, function(msg, finish)
    if msg.type == "progress" then
      if callbacks.onprogress then callbacks.onprogress(msg.value, msg.stage) end
    elseif msg.type == "result" then
      finish(); callbacks.onresult(msg.images, msg.seeds)
    elseif msg.type == "error" then
      finish(); callbacks.onerror(msg.message)
    end
  end, callbacks.onerror) }
end

-- Fetch past runs: onOk(msg) with msg.total and msg.runs.
-- preview=true returns only the first image of each run (list thumbnails).
function M.history(offset, limit, preview, onOk, onFail)
  oneShot({ type = "history", offset = offset, limit = limit,
            preview = preview }, function(msg, finish)
    finish()
    if msg.type == "history" then onOk(msg) else onFail("bad reply") end
  end, onFail)
end

-- One long-lived health socket: reopening one per tick churned sockets on the
-- UI thread (suspected alt-tab freeze). While open we just resend on it.
local pingWs, pingState, pingTimer  -- state: idle | connecting | open

local function pingDrop(onFail, msg, hard)
  if pingTimer then pingTimer:stop(); pingTimer = nil end
  if pingWs then shut(pingWs); pingWs = nil end
  pingState = "idle"
  if onFail then onFail(msg, hard) end
end

function M.ping(onOk, onFail)
  if pingState == "open" and pingWs then
    -- the peer can die between ticks, long before a CLOSE reaches us
    if not send(pingWs, { type = "ping" }) then
      pingDrop(onFail, OFFLINE, true)
    end
    return
  end
  if pingState == "connecting" then return end  -- one connect pending at a time
  pingState = "connecting"
  local ws
  ws = WebSocket{
    url = M.URL,
    deflate = false,
    onreceive = function(mt, data)
      if ws ~= pingWs then return end  -- a stale socket we already dropped
      if mt == WebSocketMessageType.OPEN then
        if pingTimer then pingTimer:stop(); pingTimer = nil end
        pingState = "open"
        if not send(ws, { type = "ping" }) then
          pingDrop(onFail, OFFLINE, true)
        end
      elseif mt == WebSocketMessageType.TEXT then
        local ok, msg = pcall(json.decode, data)
        -- msg.model: "ready" | "loading" (nil from an older server = ready);
        -- msg.progress/msg.stage: current load stage fraction and label
        if ok and msg.type == "pong" then
          onOk(msg.model, msg.progress, msg.stage)
        else pingDrop(onFail, "bad reply") end
      elseif mt == WebSocketMessageType.CLOSE then
        pingDrop(onFail, OFFLINE, true)
      end
    end,
  }
  pingWs = ws
  if Timer then
    -- a dead server never fires CLOSE; close on timeout so it stops retrying
    pingTimer = Timer{
      interval = 5.0,
      ontick = function()
        if pingState == "connecting" then
          pingDrop(onFail, OFFLINE)
        end
      end,
    }
    pingTimer:start()
  end
  ws:connect()
end

function M.pingClose()
  pingDrop(nil, nil)
end

return M

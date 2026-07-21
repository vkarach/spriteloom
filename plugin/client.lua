-- WebSocket client for the SpriteForge server. One request at a time.
local M = { URL = "http://127.0.0.1:8765" }

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
      ws:close()
    end
  end
  local function fail(msg)
    if not done then finish(); onFail(msg) end
  end
  ws = WebSocket{
    url = M.URL,
    deflate = false,
    onreceive = function(mt, data)
      if mt == WebSocketMessageType.OPEN then
        if timer then timer:stop() end
        ws:sendText(json.encode(payload))
      elseif mt == WebSocketMessageType.TEXT then
        local ok, msg = pcall(json.decode, data)
        if ok then onText(msg, finish) else fail("bad server reply") end
      elseif mt == WebSocketMessageType.CLOSE then
        fail("Server offline. Run start-server.bat.")
      end
    end,
  }
  if Timer then
    timer = Timer{ interval = 5.0,
                   ontick = function()
                     fail("Server offline. Run start-server.bat.")
                   end }
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

local function pingDrop(onFail, msg)
  if pingTimer then pingTimer:stop(); pingTimer = nil end
  if pingWs then pingWs:close(); pingWs = nil end
  pingState = "idle"
  if onFail then onFail(msg) end
end

function M.ping(onOk, onFail)
  if pingState == "open" and pingWs then
    pingWs:sendText(json.encode({ type = "ping" }))
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
        ws:sendText(json.encode({ type = "ping" }))
      elseif mt == WebSocketMessageType.TEXT then
        local ok, msg = pcall(json.decode, data)
        -- msg.model: "ready" | "loading" (nil from an older server = ready);
        -- msg.progress/msg.stage: current load stage fraction and label
        if ok and msg.type == "pong" then
          onOk(msg.model, msg.progress, msg.stage)
        else pingDrop(onFail, "bad reply") end
      elseif mt == WebSocketMessageType.CLOSE then
        pingDrop(onFail, "Server offline. Run start-server.bat.")
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
          pingDrop(onFail, "Server offline. Run start-server.bat.")
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

-- WebSocket client for the SpriteForge server. One request at a time.
local M = { URL = "http://127.0.0.1:8765" }

-- Aseprite's WebSocket takes an http:// url. json global is built in (1.3+).
function M.request(payload, callbacks)
  local ws
  local timer
  local finished = false
  local function finish()
    if not finished then
      finished = true
      if timer then timer:stop() end
      ws:close()
    end
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
        if not ok then
          finish(); callbacks.onerror("bad server reply"); return
        end
        if msg.type == "progress" then
          if callbacks.onprogress then
            callbacks.onprogress(msg.value, msg.stage)
          end
        elseif msg.type == "result" then
          finish(); callbacks.onresult(msg.images)
        elseif msg.type == "error" then
          finish(); callbacks.onerror(msg.message)
        end
      elseif mt == WebSocketMessageType.CLOSE and not finished then
        finished = true
        callbacks.onerror(
          "Server is not responding. Run start-server.bat first.")
      end
    end,
  }
  if Timer then
    timer = Timer{
      interval = 5.0,
      ontick = function()
        timer:stop()
        if not finished then
          finished = true
          ws:close()
          callbacks.onerror(
            "Server is not responding. Run start-server.bat first.")
        end
      end,
    }
    timer:start()
  end
  ws:connect()
  return { cancel = finish }
end

function M.ping(onOk, onFail)
  local ws
  local timer
  local done = false
  ws = WebSocket{
    url = M.URL,
    deflate = false,
    onreceive = function(mt, data)
      if mt == WebSocketMessageType.OPEN then
        if timer then timer:stop() end
        ws:sendText(json.encode({ type = "ping" }))
      elseif mt == WebSocketMessageType.TEXT then
        done = true; ws:close()
        local ok, msg = pcall(json.decode, data)
        if ok and msg.type == "pong" then onOk() else onFail("bad reply") end
      elseif mt == WebSocketMessageType.CLOSE and not done then
        done = true
        onFail("Server is not responding. Run start-server.bat first.")
      end
    end,
  }
  if Timer then
    timer = Timer{
      interval = 5.0,
      ontick = function()
        timer:stop()
        if not done then
          done = true
          ws:close()
          onFail("Server is not responding. Run start-server.bat first.")
        end
      end,
    }
    timer:start()
  end
  ws:connect()
end

return M

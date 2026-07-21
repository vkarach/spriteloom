-- History windows; the list and the run view are mutually recursive, so
-- they live together.
local pluginDir = ...
local ui = dofile(app.fs.joinPath(pluginDir, "ui.lua"))
local sprite = assert(loadfile(
  app.fs.joinPath(pluginDir, "sprite.lua")))(pluginDir)
local results = assert(loadfile(
  app.fs.joinPath(pluginDir, "results.lua")))(pluginDir)

local H = {}

local histMode = "list"  -- "list" | "grid"; kept across reopenings

local showHistory  -- forward declaration: showRun's Back button reopens it

-- One past run. Load its variants first, THEN open a window sized to the grid
-- (a canvas made before the images arrive can only guess its size, and
-- Aseprite then stretches it).
local function showRun(client, offset)
  client.history(offset, 1, false, function(msg)
    local run = msg.runs[1]
    if not run or not run.images or #run.images == 0 then
      ui.message("This run has no images.")
      showHistory(client)
      return
    end
    local imgs = {}
    for n, s in ipairs(run.images) do
      imgs[n] = sprite.imageFromPayload(s, "h" .. n)
    end
    results.showGrid{
      title = "SpriteForge - Run (click a variant to insert)",
      imgs = imgs, seeds = run.seeds, prefix = "SpriteForge H",
      headers = {
        { text = run.mode .. "   " .. ui.runWhen(run.name) },
        { text = run.prompt, dim = true },
      },
      onBack = function() showHistory(client) end,
    }
  end, function(err)
    ui.message(err or "Could not load the run.")
    showHistory(client)
  end)
end

-- Scrollable past runs (newest first); wheel scrolls, click opens the run.
-- Two layouts: rows with description, or a plain 3-column grid of previews.
showHistory = function(client)
  local rows = {}           -- {name, mode, prompt, count, images={b64}}
  local thumbs = {}         -- decoded as pages arrive: scroll stays free
  local scroll = 0          -- list rows or grid rows, depending on mode
  local status = "Loading history..."
  local ROWH, LISTW, VIS = 56, 600, 7
  local LISTH = ROWH * VIS
  local COLS, GW, GH, GVIS = 3, LISTW // 3, 130, 3
  -- Paged loading: ~3 screens per request, prefetching ahead of the scroll,
  -- so opening History doesn't wait for the whole archive.
  local PAGE = 21
  local nextOffset, fetching, allLoaded = 0, false, false

  local dlg = Dialog("SpriteForge - History (scroll, click a run)")

  local function maxScroll()
    if histMode == "grid" then
      return math.max(0, math.ceil(#rows / COLS) - GVIS)
    end
    return math.max(0, #rows - VIS)
  end

  local fetchMore, maybePrefetch

  fetchMore = function()
    if fetching or allLoaded then return end
    fetching = true
    client.history(nextOffset, PAGE, true, function(msg)
      nextOffset = nextOffset + PAGE
      allLoaded = nextOffset >= msg.total
      for _, run in ipairs(msg.runs) do
        rows[#rows + 1] = run
        thumbs[#rows] = sprite.imageFromPayload(run.images[1], "t" .. #rows)
      end
      fetching = false
      status = (#rows == 0 and allLoaded) and "History is empty." or nil
      dlg:repaint()
      maybePrefetch()
    end, function(err)
      fetching = false
      if #rows == 0 then status = err end
      dlg:repaint()
    end)
  end

  maybePrefetch = function()
    -- keep at least two screens beyond the current position ready
    local screen = (histMode == "grid") and GVIS * COLS or VIS
    local pos = (histMode == "grid") and (scroll + GVIS) * COLS
                or (scroll + VIS)
    if pos + 2 * screen > #rows then fetchMore() end
  end

  fetchMore()

  dlg:canvas{
    id = "hlist", width = LISTW, height = LISTH,
    onpaint = function(ev)
      local gc = ev.context
      local face, text = ui.face(), ui.text()
      gc.color = face
      gc:fillRect(Rectangle(0, 0, LISTW, LISTH))
      if status then
        gc.color = text
        gc:fillText(status, 8, 8)
        return
      end
      if histMode == "grid" then
        for v = 0, GVIS - 1 do
          for c = 0, COLS - 1 do
            local i = (scroll + v) * COLS + c + 1
            if rows[i] then
              local x, y = c * GW, v * GH
              gc.color = ui.shade(face, ((v + c) % 2 == 0) and 0.93 or 0.97)
              gc:fillRect(Rectangle(x, y, GW, GH))
              ui.drawThumb(gc, thumbs[i], x + 4, y + 4, GW - 8, GH - 8)
            end
          end
        end
      else
        for v = 1, VIS do
          local i = scroll + v
          local run = rows[i]
          if not run then break end
          local y = (v - 1) * ROWH
          gc.color = ui.shade(face, (v % 2 == 0) and 0.97 or 0.93)
          gc:fillRect(Rectangle(0, y, LISTW, ROWH))
          ui.drawThumb(gc, thumbs[i], 4, y + 4, ROWH - 8, ROWH - 8)
          gc.color = text
          gc:fillText(ui.ellipsize(run.prompt, 80), ROWH + 6, y + 10)
          gc.color = ui.shade(text, 0.65)
          local count = run.count or #run.images
          gc:fillText(string.format("%s   %s   %d variant%s", run.mode,
                                    ui.runWhen(run.name), count,
                                    count == 1 and "" or "s"),
                      ROWH + 6, y + 28)
        end
      end
      -- scrollbar
      local ms = maxScroll()
      if ms > 0 then
        local vis = (histMode == "grid") and GVIS or VIS
        local barH = math.max(16, math.floor(LISTH * vis / (vis + ms)))
        local barY = math.floor((LISTH - barH) * scroll / ms)
        gc.color = ui.shade(face, 0.80)
        gc:fillRect(Rectangle(LISTW - 5, 0, 5, LISTH))
        gc.color = ui.shade(face, 0.55)
        gc:fillRect(Rectangle(LISTW - 5, barY, 5, barH))
      end
    end,
    onwheel = function(ev)
      if #rows == 0 then return end
      local unit = (histMode == "grid") and 1 or 2
      local step = (ev.deltaY > 0) and unit or -unit
      local s = math.max(0, math.min(maxScroll(), scroll + step))
      if s ~= scroll then
        scroll = s
        dlg:repaint()
      end
      maybePrefetch()
    end,
    onmouseup = function(ev)
      if #rows == 0 then return end
      local i
      if histMode == "grid" then
        local c = math.floor(ev.x / GW)
        if c >= COLS then return end
        i = (scroll + math.floor(ev.y / GH)) * COLS + c + 1
      else
        i = scroll + math.floor(ev.y / ROWH) + 1
      end
      if rows[i] then
        dlg:close()
        showRun(client, rows[i].offset or (i - 1))  -- older server: no offset
      end
    end,
  }
  dlg:button{ id = "viewmode",
              text = (histMode == "list") and "Grid view" or "List view",
              onclick = function()
    histMode = (histMode == "list") and "grid" or "list"
    scroll = 0
    dlg:modify{ id = "viewmode",
                text = (histMode == "list") and "Grid view" or "List view" }
    dlg:repaint()
    maybePrefetch()  -- a grid screen shows more items than a list screen
  end }
  dlg:button{ text = "Close" }
  dlg:show{ wait = true }
  app.refresh()
end

H.showHistory = showHistory
H.showRun = showRun

return H

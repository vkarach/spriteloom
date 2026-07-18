-- SpriteForge panel: one control panel + a separate results window.
--
-- Hard-learned rules for Aseprite dialogs:
--  * dynamic text lives on a canvas (repaint does not relayout the dialog);
--  * after anything that can shrink/close a window, call app.refresh() to
--    repaint the vacated screen area, or a ghost strip stays behind.
local pluginDir = ...
local client = dofile(app.fs.joinPath(pluginDir, "client.lua"))
local b64 = dofile(app.fs.joinPath(pluginDir, "base64.lua"))

local D = {}

-- Settings survive across reopenings of the panel.
local last = { mode = "Generate", prompt = "", w = nil, h = nil,
               variants = 4, background = "Auto",
               view = "Side view (right)", subject = "character",
               instruction = "", symmetry = false,
               genView = "3/4 view", genSubject = "", genDetails = "" }

local BG_KEY = { ["Auto"] = "auto", ["Remove"] = "remove",
                 ["Keep"] = "keep" }

local MODE_KEY = { ["Generate"] = "generate",
                   ["Edit with AI"] = "edit",
                   ["Inpaint Selection"] = "inpaint",
                   ["Rotate / Instruct"] = "instruct" }

local PRESETS = {  -- %s = the subject; smoke-tested: naming the subject
                   -- explicitly is CRITICAL (generic "character" mutates it)
  ["Side view (right)"] = "Show the same %s from the side, facing right",
  ["Side view (left)"]  = "Show the same %s from the side, facing left",
  ["Back view"]         = "Show the same %s from behind, seen from the back",
  ["Front view"]        = "Show the same %s from the front, seen head-on",
  ["3/4 view"]          = "Show the same %s from a three-quarter view",
  ["Custom (text only)"] = "",
}
local PRESET_ORDER = { "Side view (right)", "Side view (left)", "Back view",
                       "Front view", "3/4 view", "Custom (text only)" }

-- Generate runs on Klein, which reads full prose: templates spell out the
-- camera angle explicitly (a bare "side view" tag is not enough).
local GEN_TEMPLATES = {
  ["Side view"] = "A %s, seen exactly from the side at eye level, in strict"
    .. " profile view: only its side silhouette is visible, the front face"
    .. " cannot be seen at all. A flat 2D side-scroller game object. The"
    .. " camera does not look down at it.",
  ["Front view"] = "A %s, seen straight from the front at eye level,"
    .. " head-on, perfectly centered.",
  ["3/4 view"] = "A %s in classic three-quarter view game perspective, seen"
    .. " from slightly above.",
  ["Top-down"] = "A %s seen directly from above, flat top-down game view.",
  ["Custom (text only)"] = "",
}
local GEN_VIEW_ORDER = { "3/4 view", "Side view", "Front view", "Top-down",
                         "Custom (text only)" }

-- Assemble the Generate prompt exactly as it will be sent; nil when empty.
local function assembleGenPrompt(view, subject, extra)
  local tpl = GEN_TEMPLATES[view] or ""
  if subject == "" then return nil end
  if tpl == "" then
    return extra ~= "" and (subject .. ", " .. extra) or subject
  end
  local text = string.format(tpl, subject)
  if extra ~= "" then text = text .. " " .. extra end
  return text
end

-- Assemble the instruction exactly as it will be sent; nil when empty.
local function assembleInstruction(viewPreset, subject, extra)
  local tpl = PRESETS[viewPreset] or ""
  if tpl == "" then
    return extra ~= "" and extra or nil
  end
  local text = string.format(tpl,
                             (subject ~= "" and subject) or "character")
  if extra ~= "" then text = text .. ", " .. extra end
  return text
end

local STATUS_W, STATUS_H = 282, 58  -- status line + up to 3 checklist rows

-- Colors come from the active Aseprite theme so the status area looks like
-- part of the dialog (no dark box on a light theme).
local function themeColor(name, fallback)
  local ok, c = pcall(function() return app.theme.color[name] end)
  if ok and c then return c end
  return fallback
end

local function shade(c, f)
  return Color{ r = math.floor(c.red * f), g = math.floor(c.green * f),
                b = math.floor(c.blue * f) }
end

local function tempPath(name)
  return app.fs.joinPath(app.fs.tempPath, "spriteforge_" .. name)
end

local function readFile(path)
  local f = io.open(path, "rb")
  if not f then return nil end
  local data = f:read("a"); f:close(); return data
end

local function writeFile(path, data)
  local f = assert(io.open(path, "wb")); f:write(data); f:close()
end

local function newId()
  return tostring(os.time()) .. "-" .. tostring(math.random(10000))
end

-- Flatten current frame to a PNG and return it base64-encoded.
local function exportFrame()
  local spr = app.sprite
  local img = Image(spr.width, spr.height)
  img:drawSprite(spr, app.frame.frameNumber)
  local p = tempPath("frame.png")
  img:saveAs(p)
  local data = readFile(p)
  if not data then error("could not read temp file: " .. p) end
  return b64.encode(data)
end

-- Export current selection as a white-on-black mask PNG (base64), or nil.
local function exportMask()
  local spr = app.sprite
  local sel = spr.selection
  if sel.isEmpty then return nil end
  local img = Image(spr.width, spr.height)
  for y = 0, spr.height - 1 do
    for x = 0, spr.width - 1 do
      if sel:contains(Point(x, y)) then
        img:drawPixel(x, y, Color{ r = 255, g = 255, b = 255 })
      else
        img:drawPixel(x, y, Color{ r = 0, g = 0, b = 0 })
      end
    end
  end
  local p = tempPath("mask.png")
  img:saveAs(p)
  return b64.encode(readFile(p))
end

-- Checkerboard drawn as one cached Image blit: painting it with per-square
-- fillRect cost thousands of calls per repaint and lagged the scroll.
local checkerImg
local function drawChecker(gc, dx, dy, dw, dh)
  if not checkerImg then
    local tile = Image(16, 16, ColorMode.RGB)
    local light = app.pixelColor.rgba(190, 190, 190)
    local dark = app.pixelColor.rgba(150, 150, 150)
    for y = 0, 15 do
      for x = 0, 15 do
        tile:putPixel(x, y, ((x // 8 + y // 8) % 2 == 0) and light or dark)
      end
    end
    checkerImg = Image(640, 512, ColorMode.RGB)
    for y = 0, 511, 16 do
      for x = 0, 639, 16 do
        checkerImg:drawImage(tile, Point(x, y))
      end
    end
  end
  local x = 0
  while x < dw do
    local w = math.min(640, dw - x)
    local y = 0
    while y < dh do
      local h = math.min(512, dh - y)
      gc:drawImage(checkerImg, Rectangle(0, 0, w, h),
                   Rectangle(dx + x, dy + y, w, h))
      y = y + h
    end
    x = x + w
  end
end

-- Server images arrive as raw RGBA bytes and become an in-memory Image.
-- No temp PNG + Image{fromFile}: that spammed Aseprite's Recent Files.
local function imageFromPayload(spec, n)
  if type(spec) == "string" then  -- older server still sends PNG base64
    local p = tempPath("variant" .. n .. ".png")
    writeFile(p, b64.decode(spec))
    return Image{ fromFile = p }
  end
  local img = Image(spec.w, spec.h, ColorMode.RGB)
  img.bytes = b64.decode(spec.px)
  return img
end

local function insertAsLayer(img, name)
  local spr = app.sprite
  if not spr then
    spr = Sprite(img.width, img.height)
    local layer = spr.layers[1]
    layer.name = name
    local cel = layer:cel(1)
    if cel then spr:deleteCel(cel) end
    spr:newCel(layer, 1, img, Point(0, 0))
    app.refresh()
    return { sprite = spr, layer = layer, created = true }
  end
  local layer
  app.transaction("SpriteForge: insert variant", function()
    layer = spr:newLayer()
    layer.name = name
    spr:newCel(layer, app.frame, img, Point(0, 0))
  end)
  app.refresh()
  return { sprite = spr, layer = layer }
end

local function removeInserted(entry)
  local ok = pcall(function()
    if entry.created then
      entry.sprite:close()  -- closes the untitled sprite without prompting
    else
      app.transaction("SpriteForge: remove variant", function()
        entry.sprite:deleteLayer(entry.layer)
      end)
    end
  end)
  app.refresh()
  return ok
end

-- Separate results window: fixed-size grid canvas, click to insert.
local function showResults(imgs, onInserted)
  local count = #imgs
  local iw, ih = imgs[1].width, imgs[1].height
  local cols = (count <= 2) and count or ((count <= 4) and 2 or 4)
  local rows = math.ceil(count / cols)
  local scale = math.min(600 / (cols * iw), 440 / (rows * ih))
  if scale >= 1 then scale = math.min(math.floor(scale), 6) end
  local cw, ch = math.floor(iw * scale) + 6, math.floor(ih * scale) + 6

  local inserted = {}  -- variant index -> {sprite, layer}, toggled by clicks

  local dlg = Dialog("SpriteForge - Results (click to insert / remove)")
  dlg:canvas{
    id = "grid", width = cols * cw, height = rows * ch,
    onpaint = function(ev)
      local gc = ev.context
      gc.color = themeColor("window_face", Color{ r = 200, g = 200, b = 200 })
      gc:fillRect(Rectangle(0, 0, cols * cw, rows * ch))
      for n, img in ipairs(imgs) do
        local c = (n - 1) % cols
        local r = math.floor((n - 1) / cols)
        local dx, dy = c * cw + 3, r * ch + 3
        local dw, dh = math.floor(iw * scale), math.floor(ih * scale)
        -- Checkerboard behind each variant so transparency reads clearly.
        drawChecker(gc, dx, dy, dw, dh)
        gc:drawImage(img, Rectangle(0, 0, iw, ih),
          Rectangle(dx, dy, dw, dh))
        if inserted[n] then
          gc.color = Color{ r = 106, g = 160, b = 100 }
          gc:fillRect(Rectangle(dx - 2, dy - 2, dw + 4, 2))
          gc:fillRect(Rectangle(dx - 2, dy + dh, dw + 4, 2))
          gc:fillRect(Rectangle(dx - 2, dy, 2, dh))
          gc:fillRect(Rectangle(dx + dw, dy, 2, dh))
        end
      end
    end,
    onmouseup = function(ev)
      local c = math.floor(ev.x / cw)
      local r = math.floor(ev.y / ch)
      if c < 0 or c >= cols or r < 0 then return end
      local n = r * cols + c + 1
      if not imgs[n] then return end
      if inserted[n] then
        removeInserted(inserted[n])
        inserted[n] = nil
        if onInserted then onInserted(n, false) end
      else
        inserted[n] = insertAsLayer(imgs[n], "SpriteForge " .. n)
        if onInserted then onInserted(n, inserted[n] ~= nil) end
      end
      dlg:repaint()
    end,
  }
  dlg:button{ text = "Close" }
  dlg:show{ wait = true }  -- opened from a WS callback, no nested modal loop
  app.refresh()
end

local showHistory  -- forward declaration: showRun's Back button reopens it

-- One past run: grid of its variants, click to insert (like Results).
local function showRun(offset)
  local run, imgs, inserted = nil, {}, {}
  local status = "Loading run..."
  local CW, HEAD = 600, 34
  local CH = 360 + HEAD

  local dlg = Dialog("SpriteForge - History run (click to insert / remove)")

  local function grid()
    local count = #imgs
    if count == 0 then return 1, 1, 1, 1, 1 end
    local iw, ih = imgs[1].width, imgs[1].height
    local cols = (count <= 2) and count or ((count <= 4) and 2 or 4)
    local rows = math.ceil(count / cols)
    local scale = math.min(CW / (cols * iw), (CH - HEAD) / (rows * ih))
    if scale >= 1 then scale = math.min(math.floor(scale), 6) end
    return cols, rows, math.floor(iw * scale) + 6,
           math.floor(ih * scale) + 6, scale
  end

  client.history(offset, 1, false, function(msg)
    run = msg.runs[1]
    if run then
      for n, s in ipairs(run.images) do
        imgs[n] = imageFromPayload(s, "h" .. n)
      end
      status = nil
    else
      status = "This run has no images."
    end
    dlg:repaint()
  end, function(err)
    status = err
    dlg:repaint()
  end)

  dlg:canvas{
    id = "hgrid", width = CW, height = CH,
    onpaint = function(ev)
      local gc = ev.context
      local face = themeColor("window_face", Color{ r = 200, g = 200, b = 200 })
      local text = themeColor("text", Color{ r = 40, g = 40, b = 40 })
      gc.color = face
      gc:fillRect(Rectangle(0, 0, CW, CH))
      gc.color = text
      if status then
        gc:fillText(status, 8, 8)
        return
      end
      local y4, mo, dd, hh, mi = run.name:match(
        "^(%d%d%d%d)(%d%d)(%d%d)%-(%d%d)(%d%d)")
      local when = y4 and string.format("%s-%s-%s %s:%s",
                                        y4, mo, dd, hh, mi) or ""
      gc:fillText(run.mode .. "   " .. when, 8, 4)
      local prompt = run.prompt
      if #prompt > 92 then prompt = prompt:sub(1, 92) .. "..." end
      gc.color = shade(text, 0.75)
      gc:fillText(prompt, 8, 18)
      local cols, _, cw, ch, scale = grid()
      local iw, ih = imgs[1].width, imgs[1].height
      for n, img in ipairs(imgs) do
        local c = (n - 1) % cols
        local r = math.floor((n - 1) / cols)
        local dx, dy = c * cw + 3, HEAD + r * ch + 3
        local dw, dh = math.floor(iw * scale), math.floor(ih * scale)
        drawChecker(gc, dx, dy, dw, dh)
        gc:drawImage(img, Rectangle(0, 0, iw, ih),
                     Rectangle(dx, dy, dw, dh))
        if inserted[n] then
          gc.color = Color{ r = 106, g = 160, b = 100 }
          gc:fillRect(Rectangle(dx - 2, dy - 2, dw + 4, 2))
          gc:fillRect(Rectangle(dx - 2, dy + dh, dw + 4, 2))
          gc:fillRect(Rectangle(dx - 2, dy, 2, dh))
          gc:fillRect(Rectangle(dx + dw, dy, 2, dh))
        end
      end
    end,
    onmouseup = function(ev)
      if status then return end
      local cols, _, cw, ch = grid()
      local c = math.floor(ev.x / cw)
      local r = math.floor((ev.y - HEAD) / ch)
      if c < 0 or c >= cols or r < 0 then return end
      local n = r * cols + c + 1
      if not imgs[n] then return end
      if inserted[n] then
        removeInserted(inserted[n])
        inserted[n] = nil
      else
        inserted[n] = insertAsLayer(imgs[n], "SpriteForge H" .. n)
      end
      dlg:repaint()
    end,
  }
  dlg:button{ text = "< Back", onclick = function()
    dlg:close()
    showHistory()
  end }
  dlg:button{ text = "Close" }
  dlg:show{ wait = true }
  app.refresh()
end

local histMode = "list"  -- "list" | "grid"; kept across reopenings

-- History window: scrollable past runs (newest first); mouse wheel
-- scrolls, click opens the run. Two layouts: rows with description or
-- a plain 3-column grid of previews.
showHistory = function()
  local rows = {}           -- {name, mode, prompt, count, images={b64}}
  local thumbs = {}         -- decoded as pages arrive: scroll stays free
  local scroll = 0          -- list rows or grid rows, depending on mode
  local status = "Loading history..."
  local ROWH, LISTW, VIS = 56, 600, 7
  local LISTH = ROWH * VIS
  local COLS, GW, GH, GVIS = 3, LISTW // 3, 130, 3
  -- Paged loading: only ~3 screens per request, prefetching ahead of the
  -- scroll, so opening History doesn't wait for the whole archive.
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
        thumbs[#rows] = imageFromPayload(run.images[1], "t" .. #rows)
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

  local function drawThumb(gc, img, bx, by, bw, bh)
    local s = math.min(bw / img.width, bh / img.height)
    local dw = math.max(1, math.floor(img.width * s))
    local dh = math.max(1, math.floor(img.height * s))
    local dx, dy = bx + (bw - dw) // 2, by + (bh - dh) // 2
    drawChecker(gc, dx, dy, dw, dh)
    gc:drawImage(img, Rectangle(0, 0, img.width, img.height),
                 Rectangle(dx, dy, dw, dh))
  end

  dlg:canvas{
    id = "hlist", width = LISTW, height = LISTH,
    onpaint = function(ev)
      local gc = ev.context
      local face = themeColor("window_face", Color{ r = 200, g = 200, b = 200 })
      local text = themeColor("text", Color{ r = 40, g = 40, b = 40 })
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
            local run = rows[i]
            if run then
              local x, y = c * GW, v * GH
              gc.color = shade(face, ((v + c) % 2 == 0) and 0.93 or 0.97)
              gc:fillRect(Rectangle(x, y, GW, GH))
              drawThumb(gc, thumbs[i], x + 4, y + 4, GW - 8, GH - 8)
            end
          end
        end
      else
        for v = 1, VIS do
          local i = scroll + v
          local run = rows[i]
          if not run then break end
          local y = (v - 1) * ROWH
          gc.color = shade(face, (v % 2 == 0) and 0.97 or 0.93)
          gc:fillRect(Rectangle(0, y, LISTW, ROWH))
          drawThumb(gc, thumbs[i], 4, y + 4, ROWH - 8, ROWH - 8)
          local prompt = run.prompt
          if #prompt > 80 then prompt = prompt:sub(1, 80) .. "..." end
          gc.color = text
          gc:fillText(prompt, ROWH + 6, y + 10)
          local y4, mo, dd, hh, mi = run.name:match(
            "^(%d%d%d%d)(%d%d)(%d%d)%-(%d%d)(%d%d)")
          local when = y4 and string.format("%s-%s-%s %s:%s",
                                            y4, mo, dd, hh, mi) or ""
          gc.color = shade(text, 0.65)
          local count = run.count or #run.images
          gc:fillText(string.format("%s   %s   %d variant%s", run.mode, when,
                                    count, count == 1 and "" or "s"),
                      ROWH + 6, y + 28)
        end
      end
      -- scrollbar
      local ms = maxScroll()
      if ms > 0 then
        local vis = (histMode == "grid") and GVIS or VIS
        local totalRows = vis + ms
        local barH = math.max(16, math.floor(LISTH * vis / totalRows))
        local barY = math.floor((LISTH - barH) * scroll / ms)
        gc.color = shade(face, 0.80)
        gc:fillRect(Rectangle(LISTW - 5, 0, 5, LISTH))
        gc.color = shade(face, 0.55)
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
        showRun(rows[i].offset or (i - 1))  -- older server: no offset field
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

function D.open()
  if D._isOpen then return end

  local state = "idle"        -- idle | running | done | error
  local statusText = "Set the parameters and press Run."
  local progress = 0
  local job = nil
  local serverStatus = "checking"  -- checking | online | warming | offline
  local loadProgress = 0    -- 0..1 model load fraction while warming
  local pingBusy = false
  local pingAt = 0          -- watchdog: never let a lost ping jam the loop
  local pingMisses = 0      -- debounce: one lost ping is not "offline"
  local pingTimer
  local pingInterval = 10.0
  local updateHint, retunePing  -- forward: checkServer uses both
  local sizeTimer
  local animTimer   -- drives the busy animation only while running
  local baseW, baseH  -- nil = re-capture on next guard tick

  local dlg

  local function repaint() dlg:repaint() end

  local function checkServer()
    if pingBusy and os.clock() - pingAt < 8 then return end
    pingBusy = true
    pingAt = os.clock()
    client.ping(
      function(model, progress)
        pingBusy = false
        pingMisses = 0
        -- server preloads Klein at startup; show it until the model is in
        serverStatus = (model == "loading") and "warming" or "online"
        loadProgress = progress or 0
        -- a stale "Server offline" error must clear once the server answers
        if state == "error" and statusText:find("offline") then
          updateHint()
        end
        retunePing()
        repaint()
      end,
      function()
        pingBusy = false
        pingMisses = pingMisses + 1
        -- a live server can miss one ping while the model load hogs it;
        -- flip to offline only when it was never seen alive or misses twice
        local alive = serverStatus == "online" or serverStatus == "warming"
        if not alive or pingMisses >= 2 then
          serverStatus = "offline"
        end
        retunePing()
        repaint()
      end)
  end

  -- Warming wants a lively bar (1s pings); otherwise 10s is plenty.
  function retunePing()
    if not Timer or not pingTimer then return end
    local want = (serverStatus == "warming") and 1.0 or 10.0
    if pingInterval == want then return end
    pingInterval = want
    pingTimer:stop()
    pingTimer = Timer{ interval = want, ontick = checkServer }
    pingTimer:start()
  end

  local function setState(s, text)
    state = s
    statusText = text
    local running = (s == "running")
    dlg:modify{ id = "run", enabled = not running }
    dlg:modify{ id = "cancel", enabled = running }
    if Timer then
      if running and not animTimer then
        animTimer = Timer{ interval = 0.08, ontick = repaint }
        animTimer:start()
      elseif not running and animTimer then
        animTimer:stop()
        animTimer = nil
      end
    end
    repaint()
  end

  -- One consistent pattern for every mode: a checklist of requirements
  -- (drawn in the status canvas as checkboxes) + one status line.
  local function requirements()
    local d = dlg.data
    local m = d.mode
    local spr = app.sprite
    local reqs = {}
    if m == "Generate" then
      local text = assembleGenPrompt(d.genView, d.genSubject, d.genDetails)
      if text then
        if #text > 34 then text = text:sub(1, 34) .. "..." end
        reqs[1] = { true, 'Will send: "' .. text .. '"' }
      else
        reqs[1] = { false, "Subject describes what to generate" }
      end
    elseif m == "Edit with AI" then
      reqs[1] = { spr ~= nil, "A sprite is open" }
      reqs[2] = { d.prompt ~= "", "Prompt describes the change" }
    elseif m == "Inpaint Selection" then
      reqs[1] = { spr ~= nil, "A sprite is open" }
      reqs[2] = { (spr ~= nil) and not spr.selection.isEmpty,
                  "A region is selected (rectangle/lasso)" }
      reqs[3] = { d.prompt ~= "", "Prompt describes the region content" }
    else -- Rotate / Instruct
      reqs[1] = { spr ~= nil, "A sprite is open" }
      local text = assembleInstruction(d.viewPreset, d.subject, d.instruction)
      if text then
        if #text > 34 then text = text:sub(1, 34) .. "..." end
        reqs[2] = { true, 'Will send: "' .. text .. '"' }
      else
        reqs[2] = { false, "Pick a view preset or type an instruction" }
      end
    end
    return reqs
  end

  local function reqSignature(reqs)
    local sig = dlg.data.mode
    for _, r in ipairs(reqs) do
      sig = sig .. (r[1] and "1" or "0") .. r[2]
    end
    return sig
  end

  local lastReqSig = nil

  function updateHint()  -- assigns the forward-declared local
    if state == "running" then return end
    local reqs = requirements()
    lastReqSig = reqSignature(reqs)
    local allMet = true
    for _, r in ipairs(reqs) do allMet = allMet and r[1] end
    setState("idle", allMet and "Ready - press Run."
                             or "Complete the checklist:")
  end

  local function applyModeVisibility()
    local m = dlg.data.mode
    local instruct = m == "Rotate / Instruct"
    dlg:modify{ id = "sizeSep", visible = m == "Generate" }
    dlg:modify{ id = "w", visible = m == "Generate" }
    dlg:modify{ id = "h", visible = m == "Generate" }
    dlg:modify{ id = "genView", visible = m == "Generate" }
    dlg:modify{ id = "genSubject", visible = m == "Generate" }
    dlg:modify{ id = "genDetails", visible = m == "Generate" }
    dlg:modify{ id = "prompt",
                visible = m == "Edit with AI" or m == "Inpaint Selection" }
    dlg:modify{ id = "viewPreset", visible = instruct }
    dlg:modify{ id = "subject", visible = instruct }
    dlg:modify{ id = "instruction", visible = instruct }
    dlg:modify{ id = "symmetry", visible = instruct }
    app.refresh()
    baseW, baseH = nil, nil  -- legit relayout: size guard re-captures
    updateHint()
  end

  local function startRun()
    if state == "running" then return end
    local d = dlg.data
    last.mode = d.mode; last.prompt = d.prompt
    last.w = d.w; last.h = d.h
    last.variants = d.variants
    last.background = d.background

    local mode = MODE_KEY[d.mode]
    local payload = { id = newId(), mode = mode,
                      variants = d.variants, frames = {},
                      background = BG_KEY[d.background] or "auto" }
    if mode == "instruct" then
      local spr = app.sprite
      if not spr then
        setState("error", "Open a sprite first.")
        return
      end
      local instruction = assembleInstruction(d.viewPreset, d.subject,
                                              d.instruction)
      if not instruction then
        setState("error", "Pick a view preset or type an instruction.")
        return
      end
      last.view = d.viewPreset; last.subject = d.subject
      last.instruction = d.instruction; last.symmetry = d.symmetry
      payload.prompt = instruction
      payload.symmetry = d.symmetry
      payload.target_size = { spr.width, spr.height }
      payload.frames = { { image = exportFrame() } }
    elseif mode == "generate" then
      local text = assembleGenPrompt(d.genView, d.genSubject, d.genDetails)
      if not text then
        setState("error", "Subject is empty.")
        return
      end
      last.genView = d.genView; last.genSubject = d.genSubject
      last.genDetails = d.genDetails
      payload.prompt = text
      payload.target_size = { d.w, d.h }
    else
      if d.prompt == "" then
        setState("error", "Prompt is empty.")
        return
      end
      local spr = app.sprite
      if not spr then
        setState("error", "Open a sprite first.")
        return
      end
      payload.prompt = d.prompt
      payload.target_size = { spr.width, spr.height }
      if mode == "edit" then
        payload.frames = { { image = exportFrame() } }
      else
        local mask = exportMask()
        if not mask then
          setState("error", "No selection. Select the region to redraw first.")
          return
        end
        payload.frames = { { image = exportFrame(), mask = mask } }
      end
    end

    progress = 0
    setState("running", "Contacting server...")

    job = client.request(payload, {
      onprogress = function(v, stage)
        -- A stage message (model load / decode / postprocess) only relabels;
        -- the bar stays where it is - full after generation, empty (animated)
        -- before it during a model load. Numeric ticks fill the bar.
        if stage then
          statusText = stage
        else
          progress = v
          statusText = "Generating"
        end
        repaint()
      end,
      onresult = function(images)
        local imgs = {}
        for n, s in ipairs(images) do imgs[n] = imageFromPayload(s, n) end
        setState("done", string.format(
          "%d variants ready. Press Run for more.", #imgs))
        showResults(imgs, function(n, added)
          if added then
            setState("done", "Inserted variant " .. n ..
                             " (click it again to remove).")
          else
            setState("done", "Removed variant " .. n .. ".")
          end
        end)
      end,
      onerror = function(msg)
        setState("error", msg)
      end,
    })
  end

  dlg = Dialog{
    title = "SpriteForge",
    onclose = function()
      D._isOpen = false
      if pingTimer then pingTimer:stop() end
      if sizeTimer then sizeTimer:stop() end
      if animTimer then animTimer:stop() end
      if job and state == "running" then job.cancel() end
    end,
  }

  dlg:separator{ text = "Task" }
  dlg:combobox{ id = "mode", option = last.mode,
                options = { "Generate", "Edit with AI", "Inpaint Selection",
                            "Rotate / Instruct" },
                onchange = applyModeVisibility }
  dlg:combobox{ id = "genView", label = "View:", option = last.genView,
                options = GEN_VIEW_ORDER, onchange = updateHint,
                visible = last.mode == "Generate" }
  dlg:entry{ id = "genSubject", label = "Subject:", text = last.genSubject,
             focus = true, onchange = updateHint,
             visible = last.mode == "Generate" }
  dlg:entry{ id = "genDetails", label = "Extra:", text = last.genDetails,
             onchange = updateHint, visible = last.mode == "Generate" }
  dlg:entry{ id = "prompt", label = "Prompt:", text = last.prompt,
             onchange = updateHint,
             visible = last.mode == "Edit with AI"
                       or last.mode == "Inpaint Selection" }
  dlg:combobox{ id = "viewPreset", label = "View:", option = last.view,
                options = PRESET_ORDER, onchange = updateHint,
                visible = last.mode == "Rotate / Instruct" }
  dlg:entry{ id = "subject", label = "Subject:", text = last.subject,
             onchange = updateHint,
             visible = last.mode == "Rotate / Instruct" }
  dlg:entry{ id = "instruction", label = "Extra:",
             text = last.instruction, onchange = updateHint,
             visible = last.mode == "Rotate / Instruct" }
  dlg:check{ id = "symmetry", text = "Mirror symmetry (front/back views)",
             selected = last.symmetry,
             visible = last.mode == "Rotate / Instruct" }
  local spr = app.sprite
  dlg:separator{ id = "sizeSep", text = "Size",
                 visible = last.mode == "Generate" }
  dlg:number{ id = "w", label = "Width:", hexpand = false,
              text = tostring(last.w or (spr and spr.width) or 64),
              visible = last.mode == "Generate" }
  dlg:number{ id = "h", label = "Height:", hexpand = false,
              text = tostring(last.h or (spr and spr.height) or 64),
              visible = last.mode == "Generate" }
  dlg:separator{ text = "Options" }
  dlg:slider{ id = "variants", label = "Variants:", min = 1, max = 8,
              value = last.variants }
  dlg:combobox{ id = "background", label = "Background:",
                option = last.background,
                options = { "Auto", "Remove", "Keep" } }
  dlg:separator{ text = "Status" }
  dlg:canvas{
    id = "view", width = STATUS_W, height = STATUS_H,
    onpaint = function(ev)
      local gc = ev.context
      local face = themeColor("window_face", Color{ r = 200, g = 200, b = 200 })
      -- Inset status panel: slightly darker than the dialog with a border,
      -- so the text sits in a visible container instead of floating bare.
      gc.color = face
      gc:fillRect(Rectangle(0, 0, STATUS_W, STATUS_H))
      gc.color = shade(face, 0.72)
      gc:fillRect(Rectangle(0, 0, STATUS_W, STATUS_H))
      gc.color = shade(face, 0.93)
      gc:fillRect(Rectangle(1, 1, STATUS_W - 2, STATUS_H - 2))
      if state == "error" then
        gc.color = Color{ r = 168, g = 82, b = 62 }
      else
        gc.color = themeColor("text", Color{ r = 40, g = 40, b = 40 })
      end
      local line = statusText
      if state == "running" then
        line = line:gsub("%.+$", "")
        line = line .. string.rep(".", math.floor(os.clock() * 3) % 4)
      end
      -- ellipsize so a long stage message never collides with the
      -- server-status word on the right
      local maxW = STATUS_W - 62
      local fits = function(s)
        local ok, size = pcall(function() return gc:measureText(s) end)
        return not ok or not size or size.width <= maxW
      end
      if not fits(line) then
        while #line > 1 and not fits(line .. "...") do
          line = line:sub(1, #line - 1)
        end
        line = line .. "..."
      end
      gc:fillText(line, 8, 6)
      local srv = { online = { Color{ r = 106, g = 160, b = 100 }, "online" },
                    warming = { Color{ r = 212, g = 180, b = 74 }, "loading" },
                    offline = { Color{ r = 168, g = 82, b = 62 }, "offline" },
                    checking = { Color{ r = 212, g = 180, b = 74 }, "..." } }
      local dot, word = srv[serverStatus][1], srv[serverStatus][2]
      local wordW = 6 * #word
      local ok, size = pcall(function() return gc:measureText(word) end)
      if ok and size then wordW = size.width end
      gc.color = dot
      gc:fillRect(Rectangle(STATUS_W - 14, 7, 6, 6))
      gc.color = shade(themeColor("text", Color{ r = 40, g = 40, b = 40 }),
                       0.8)
      gc:fillText(word, STATUS_W - 18 - wordW, 6)
      if state == "running" then
        -- Monochrome bar in theme shades, same family as the inset panel.
        local bx, by, bw, bh = 8, 22, STATUS_W - 16, 10
        gc.color = shade(face, 0.85)
        gc:fillRect(Rectangle(bx, by, bw, bh))
        gc.color = shade(face, 0.55)
        if progress > 0 then
          gc:fillRect(Rectangle(bx + 1, by + 1,
                                math.floor((bw - 2) * progress), bh - 2))
          gc.color = shade(themeColor("text", Color{ r = 40, g = 40, b = 40 }),
                           0.75)
          gc:fillText(string.format("%d%%", math.floor(progress * 100)),
                      math.floor(STATUS_W / 2) - 8, 38)
        else
          local seg = math.floor((bw - 2) * 0.25)
          local ph = (os.clock() * 0.8) % 1
          local tri = ph < 0.5 and (ph * 2) or (2 - ph * 2)
          local x = bx + 1 + math.floor((bw - 2 - seg) * tri)
          gc:fillRect(Rectangle(x, by + 1, seg, bh - 2))
        end
      elseif serverStatus == "checking" then
        -- first ping still in flight: say so instead of a bare "..."
        gc.color = shade(themeColor("text", Color{ r = 40, g = 40, b = 40 }),
                         0.75)
        gc:fillText("Connecting to server...", 8, 38)
      elseif serverStatus == "warming" then
        -- Model load bar: same style as the run bar, fed by ping progress.
        local bx, by, bw, bh = 8, 22, STATUS_W - 16, 10
        gc.color = shade(face, 0.85)
        gc:fillRect(Rectangle(bx, by, bw, bh))
        if loadProgress > 0 then
          gc.color = shade(face, 0.55)
          gc:fillRect(Rectangle(bx + 1, by + 1,
                                math.floor((bw - 2) * loadProgress), bh - 2))
        end
        gc.color = shade(themeColor("text", Color{ r = 40, g = 40, b = 40 }),
                         0.75)
        local label = (loadProgress > 0)
          and string.format("Loading Klein model  %d%%",
                            math.floor(loadProgress * 100))
          or "Loading Klein model..."
        gc:fillText(label, bx, 38)
      else
        -- Checklist drawn as a tree hanging off the status line.
        local textCol = themeColor("text", Color{ r = 40, g = 40, b = 40 })
        local green = Color{ r = 106, g = 160, b = 100 }
        local guide = shade(face, 0.70)
        local reqs = requirements()
        for i, r in ipairs(reqs) do
          local y = 16 + (i - 1) * 13
          local cy = y + 4
          gc.color = guide
          gc:fillRect(Rectangle(14, cy, 8, 1))          -- branch stub
          if i < #reqs then
            gc:fillRect(Rectangle(14, cy, 1, 13))       -- trunk down
          end
          if i == 1 then
            gc:fillRect(Rectangle(14, 14, 1, cy - 14))  -- trunk from title
          end
          if r[1] then
            gc.color = green
            gc:fillRect(Rectangle(26, y, 9, 9))
            -- Proper stroked tick; if the path API is missing, the plain
            -- green box alone still reads as "done".
            pcall(function()
              gc.color = Color{ r = 245, g = 245, b = 240 }
              gc.strokeWidth = 2
              gc:beginPath()
              gc:moveTo(28, y + 5)
              gc:lineTo(30, y + 7)
              gc:lineTo(33, y + 2)
              gc:stroke()
            end)
          else
            gc.color = shade(face, 0.60)
            gc:fillRect(Rectangle(26, y, 9, 9))
            gc.color = shade(face, 0.93)
            gc:fillRect(Rectangle(27, y + 1, 7, 7))
          end
          gc.color = r[1] and shade(textCol, 0.85) or textCol
          gc:fillText(r[2], 40, y + 2)
        end
      end
    end,
  }
  dlg:button{ id = "run", text = "Run", onclick = startRun }
  dlg:button{ id = "cancel", text = "Cancel", enabled = false,
              onclick = function()
    if job then job.cancel() end
    setState("idle", "Cancelled. Press Run to try again.")
  end }
  dlg:button{ id = "historybtn", text = "History", onclick = showHistory }
  dlg:button{ id = "closebtn", text = "Close", onclick = function()
    dlg:close()
  end }

  D._isOpen = true
  updateHint()
  if Timer then
    pingTimer = Timer{ interval = 10.0, ontick = checkServer }
    pingTimer:start()
    -- The Dialog API has no "not resizable" flag; snap the size back if the
    -- user drags an edge (moving the window stays allowed).
    sizeTimer = Timer{ interval = 0.5, ontick = function()
      local nb = dlg.bounds
      if not baseW then
        baseW, baseH = nb.width, nb.height
      elseif nb.width ~= baseW or nb.height ~= baseH then
        dlg.bounds = Rectangle(nb.x, nb.y, baseW, baseH)
        app.refresh()
      end
      -- Checklist state can change outside the dialog (selection made on
      -- the canvas, sprite closed) - refresh when it actually did.
      if state ~= "running" then
        local sig = reqSignature(requirements())
        if sig ~= lastReqSig then updateHint() end
      end
    end }
    sizeTimer:start()
  end
  checkServer()
  dlg:show{ wait = false }
end

return D

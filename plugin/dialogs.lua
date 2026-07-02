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
               strength = 60, variants = 4,
               view = "Side view (right)", subject = "character",
               instruction = "", symmetry = false }

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

local STATUS_W, STATUS_H = 380, 58  -- status line + up to 3 checklist rows

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

local function imageFromB64(s, n)
  local p = tempPath("variant" .. n .. ".png")
  writeFile(p, b64.decode(s))
  return Image{ fromFile = p }
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
        for qy = 0, dh - 1, 8 do
          for qx = 0, dw - 1, 8 do
            if ((qx + qy) // 8) % 2 == 0 then
              gc.color = Color{ r = 190, g = 190, b = 190 }
            else
              gc.color = Color{ r = 150, g = 150, b = 150 }
            end
            gc:fillRect(Rectangle(dx + qx, dy + qy,
              math.min(8, dw - qx), math.min(8, dh - qy)))
          end
        end
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

function D.open()
  if D._isOpen then return end

  local state = "idle"        -- idle | running | done | error
  local statusText = "Set the parameters and press Run."
  local progress = 0
  local job = nil
  local serverStatus = "checking"  -- checking | online | offline
  local pingBusy = false
  local pingTimer
  local sizeTimer
  local animTimer   -- drives the busy animation only while running
  local baseW, baseH  -- nil = re-capture on next guard tick

  local dlg

  local function repaint() dlg:repaint() end

  local function checkServer()
    if pingBusy then return end
    pingBusy = true
    client.ping(
      function() pingBusy = false; serverStatus = "online"; repaint() end,
      function() pingBusy = false; serverStatus = "offline"; repaint() end)
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
      reqs[1] = { d.prompt ~= "", "Prompt describes what to generate" }
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
        if #text > 40 then text = text:sub(1, 40) .. "..." end
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

  local function updateHint()
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
    dlg:modify{ id = "w", visible = m == "Generate" }
    dlg:modify{ id = "h", visible = m == "Generate" }
    dlg:modify{ id = "strength", visible = m == "Edit with AI" }
    dlg:modify{ id = "prompt", visible = not instruct }
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
    last.strength = d.strength; last.variants = d.variants

    local mode = MODE_KEY[d.mode]
    local payload = { id = newId(), mode = mode,
                      variants = d.variants, frames = {} }
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
      if d.prompt == "" then
        setState("error", "Prompt is empty.")
        return
      end
      payload.prompt = d.prompt
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
        payload.strength = d.strength / 100
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
        progress = v
        if stage then
          statusText = stage  -- e.g. "Loading klein model..." during a swap
        elseif v < 0.85 then
          statusText = "Generating"
        elseif v < 0.95 then
          statusText = "Decoding images"
        else
          statusText = "Post-processing"
        end
        repaint()
      end,
      onresult = function(images)
        local imgs = {}
        for n, s in ipairs(images) do imgs[n] = imageFromB64(s, n) end
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

  dlg:combobox{ id = "mode", label = "Task:", option = last.mode,
                options = { "Generate", "Edit with AI", "Inpaint Selection",
                            "Rotate / Instruct" },
                onchange = applyModeVisibility }
  dlg:entry{ id = "prompt", label = "Prompt:", text = last.prompt,
             onchange = updateHint,
             visible = last.mode ~= "Rotate / Instruct" }
  dlg:combobox{ id = "viewPreset", label = "View:", option = last.view,
                options = PRESET_ORDER, onchange = updateHint,
                visible = last.mode == "Rotate / Instruct" }
  dlg:entry{ id = "subject", label = "Subject:", text = last.subject,
             onchange = updateHint,
             visible = last.mode == "Rotate / Instruct" }
  dlg:entry{ id = "instruction", label = "Extra:", text = last.instruction,
             onchange = updateHint,
             visible = last.mode == "Rotate / Instruct" }
  dlg:check{ id = "symmetry", text = "Mirror symmetry (front/back views)",
             selected = last.symmetry,
             visible = last.mode == "Rotate / Instruct" }
  local spr = app.sprite
  dlg:number{ id = "w", label = "Size:",
              text = tostring(last.w or (spr and spr.width) or 64),
              visible = last.mode == "Generate" }
  dlg:number{ id = "h",
              text = tostring(last.h or (spr and spr.height) or 64),
              visible = last.mode == "Generate" }
  dlg:slider{ id = "strength", label = "Strength %:", min = 20, max = 90,
              value = last.strength, visible = last.mode == "Edit with AI" }
  dlg:slider{ id = "variants", label = "Variants:", min = 1, max = 8,
              value = last.variants }
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
      gc:fillText(line, 8, 6)
      local srv = { online = { Color{ r = 106, g = 160, b = 100 }, "online" },
                    offline = { Color{ r = 168, g = 82, b = 62 }, "offline" },
                    checking = { Color{ r = 160, g = 140, b = 80 }, "..." } }
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
      else
        local textCol = themeColor("text", Color{ r = 40, g = 40, b = 40 })
        local green = Color{ r = 106, g = 160, b = 100 }
        for i, r in ipairs(requirements()) do
          local y = 16 + (i - 1) * 13
          if r[1] then
            gc.color = green
            gc:fillRect(Rectangle(8, y, 9, 9))
            -- Proper stroked tick; if the path API is missing, the plain
            -- green box alone still reads as "done".
            pcall(function()
              gc.color = Color{ r = 245, g = 245, b = 240 }
              gc.strokeWidth = 2
              gc:beginPath()
              gc:moveTo(10, y + 5)
              gc:lineTo(12, y + 7)
              gc:lineTo(15, y + 2)
              gc:stroke()
            end)
          else
            gc.color = shade(face, 0.60)
            gc:fillRect(Rectangle(8, y, 9, 9))
            gc.color = shade(face, 0.93)
            gc:fillRect(Rectangle(9, y + 1, 7, 7))
          end
          gc.color = r[1] and shade(textCol, 0.85) or textCol
          gc:fillText(r[2], 22, y + 2)
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

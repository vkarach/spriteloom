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

local STATUS_W, STATUS_H = 380, 42

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
  if not spr then return end
  app.transaction("SpriteForge: insert variant", function()
    local layer = spr:newLayer()
    layer.name = name
    spr:newCel(layer, app.frame, img, Point(0, 0))
  end)
  app.refresh()
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

  local dlg = Dialog("SpriteForge - Results (click to insert)")
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
      end
    end,
    onmouseup = function(ev)
      local c = math.floor(ev.x / cw)
      local r = math.floor(ev.y / ch)
      if c < 0 or c >= cols or r < 0 then return end
      local n = r * cols + c + 1
      if imgs[n] then
        insertAsLayer(imgs[n], "SpriteForge " .. n)
        if onInserted then onInserted(n) end
      end
    end,
  }
  dlg:button{ text = "Close" }
  dlg:show{ wait = true }  -- opened from a WS callback, no nested modal loop
  app.refresh()            -- repaint the area this window occupied
end

function D.open()
  if D._isOpen then return end

  local state = "idle"        -- idle | running | done | error
  local statusText = "Set the parameters and press Run."
  local progress = 0
  local job = nil

  local dlg

  local function repaint() dlg:repaint() end

  local function setState(s, text)
    state = s
    statusText = text
    local running = (s == "running")
    dlg:modify{ id = "run", enabled = not running }
    dlg:modify{ id = "cancel", enabled = running }
    repaint()
  end

  -- Mode-dependent fields disappear entirely; refresh afterwards because
  -- the dialog may shrink and leave an unpainted strip.  Every mode gets a
  -- neutral hint about what Run will do (red is reserved for real errors).
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
    if state == "running" then return end
    local spr = app.sprite
    if m == "Generate" then
      setState("idle", string.format("Run will generate a new %dx%d sprite.",
                                     dlg.data.w, dlg.data.h))
    elseif m == "Edit with AI" then
      setState("idle", "Run will repaint the current sprite by your prompt.")
    elseif instruct then
      setState("idle",
        "Run will redraw the sprite per the instruction (model swap ~30s).")
    else
      if not spr or spr.selection.isEmpty then
        setState("idle",
          "Select a region (rectangle/lasso), then press Run.")
      else
        local b = spr.selection.bounds
        setState("idle", string.format(
          "Run will redraw the selected %dx%d region.", b.width, b.height))
      end
    end
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
      local tpl = PRESETS[d.viewPreset] or ""
      local subject = (d.subject ~= "" and d.subject) or "character"
      local extra = d.instruction or ""
      local instruction
      if tpl == "" then
        instruction = extra
      else
        instruction = string.format(tpl, subject)
        if extra ~= "" then instruction = instruction .. ", " .. extra end
      end
      if instruction == "" then
        setState("error", "Pick a view preset or type an instruction.")
        return
      end
      last.view = d.viewPreset; last.subject = d.subject
      last.instruction = extra; last.symmetry = d.symmetry
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
          statusText = string.format("Generating... %d%%", math.floor(v * 100))
        elseif v < 0.95 then
          statusText = "Decoding images..."
        else
          statusText = "Post-processing..."
        end
        repaint()
      end,
      onresult = function(images)
        local imgs = {}
        for n, s in ipairs(images) do imgs[n] = imageFromB64(s, n) end
        setState("done", string.format(
          "%d variants ready. Press Run for more.", #imgs))
        showResults(imgs, function(n)
          setState("done", "Inserted variant " .. n .. " as a new layer.")
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
      if job and state == "running" then job.cancel() end
    end,
  }

  dlg:combobox{ id = "mode", label = "Task:", option = last.mode,
                options = { "Generate", "Edit with AI", "Inpaint Selection",
                            "Rotate / Instruct" },
                onchange = applyModeVisibility }
  dlg:entry{ id = "prompt", label = "Prompt:", text = last.prompt,
             visible = last.mode ~= "Rotate / Instruct" }
  dlg:combobox{ id = "viewPreset", label = "View:", option = last.view,
                options = PRESET_ORDER,
                visible = last.mode == "Rotate / Instruct" }
  dlg:entry{ id = "subject", label = "Subject:", text = last.subject,
             visible = last.mode == "Rotate / Instruct" }
  dlg:entry{ id = "instruction", label = "Extra:", text = last.instruction,
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
        gc.color = Color{ r = 200, g = 60, b = 50 }
      else
        gc.color = themeColor("text", Color{ r = 40, g = 40, b = 40 })
      end
      gc:fillText(statusText, 8, 8)
      if state == "running" then
        local bx, by, bw, bh = 8, 25, STATUS_W - 16, 10
        gc.color = shade(face, 0.80)
        gc:fillRect(Rectangle(bx, by, bw, bh))
        gc.color = themeColor("selected", Color{ r = 90, g = 130, b = 200 })
        gc:fillRect(Rectangle(bx + 1, by + 1,
                              math.floor((bw - 2) * progress), bh - 2))
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
  dlg:show{ wait = false }
end

return D

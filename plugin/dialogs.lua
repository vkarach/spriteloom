-- All SpriteForge dialogs. Loaded by main.lua with the plugin dir.
local pluginDir = ...
local client = dofile(app.fs.joinPath(pluginDir, "client.lua"))
local b64 = dofile(app.fs.joinPath(pluginDir, "base64.lua"))

local D = {}

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

-- Flatten current frame to a PNG and return it base64-encoded.
function D._exportFrame()
  local spr = app.sprite
  local img = Image(spr.width, spr.height)
  img:drawSprite(spr, app.frame.frameNumber)
  local p = tempPath("frame.png")
  img:saveAs(p)
  local data = readFile(p)
  if not data then error("could not read temp file: " .. p) end
  return b64.encode(data)
end

-- Decode a base64 PNG into an Aseprite Image.
local function imageFromB64(s, n)
  local p = tempPath("variant" .. n .. ".png")
  writeFile(p, b64.decode(s))
  return Image{ fromFile = p }
end

-- Insert an image as a new layer of the active sprite.
local function insertAsLayer(img, name)
  local spr = app.sprite
  if not spr then app.alert("No sprite is open to insert into.") return end
  app.transaction("SpriteForge: insert variant", function()
    local layer = spr:newLayer()
    layer.name = name
    spr:newCel(layer, app.frame, img, Point(0, 0))
  end)
  app.refresh()
end

local function newId()
  return tostring(os.time()) .. "-" .. tostring(math.random(10000))
end

-- Preview grid: variants drawn scaled-up; click inserts as a layer.
function D._previewGrid(imagesB64, payload)
  local imgs = {}
  for n, s in ipairs(imagesB64) do imgs[n] = imageFromB64(s, n) end
  local cols = math.min(#imgs, 2)
  local rows = math.ceil(#imgs / cols)
  local iw, ih = imgs[1].width, imgs[1].height
  local scale = math.max(1, math.floor(192 / math.max(iw, ih)))
  local cw, ch = iw * scale + 8, ih * scale + 8

  local dlg = Dialog("SpriteForge — pick a variant (click)")
  dlg:canvas{
    id = "grid", width = cols * cw, height = rows * ch,
    onpaint = function(ev)
      local gc = ev.context
      for n, img in ipairs(imgs) do
        local c, r = (n - 1) % cols, math.floor((n - 1) / cols)
        gc:drawImage(img, Rectangle(0, 0, iw, ih),
          Rectangle(c * cw + 4, r * ch + 4, iw * scale, ih * scale))
      end
    end,
    onmouseup = function(ev)
      local n = math.floor(ev.x / cw) + cols * math.floor(ev.y / ch) + 1
      if imgs[n] then
        insertAsLayer(imgs[n], "SpriteForge " .. n)
        app.alert("Inserted variant " .. n .. " as a new layer.")
      end
    end,
  }
  if payload then
    dlg:button{ text = "More variants", onclick = function()
      dlg:close()
      local p = {}
      for k, v in pairs(payload) do p[k] = v end
      p.id = newId()
      D._runJob(p)
    end }
  end
  dlg:button{ text = "Close" }
  dlg:show{ wait = false }
end

-- Progress dialog + request lifecycle shared by all modes.
function D._runJob(payload)
  local dlg = Dialog("SpriteForge — generating...")
  local job
  dlg:label{ id = "status", text = "Contacting server...      " }
  dlg:button{ text = "Cancel", onclick = function()
    if job then job.cancel() end
    dlg:close()
  end }
  dlg:show{ wait = false }

  job = client.request(payload, {
    onprogress = function(v)
      local text
      if v < 0.85 then
        text = string.format("Generating... %d%%", math.floor(v * 100))
      elseif v < 0.95 then
        text = "Decoding images (takes a moment)..."
      else
        text = "Post-processing..."
      end
      dlg:modify{ id = "status", text = text }
    end,
    onresult = function(images)
      dlg:close()
      D._previewGrid(images, payload)
    end,
    onerror = function(msg)
      dlg:close()
      app.alert("SpriteForge: " .. msg)
    end,
  })
end

function D.generate()
  if not app.sprite then app.alert("Open a sprite first.") return end
  local spr = app.sprite
  local dlg = Dialog("SpriteForge — Generate")
  dlg:entry{ id = "prompt", label = "Prompt:", focus = true }
  dlg:number{ id = "w", label = "Size:", text = tostring(spr.width) }
  dlg:number{ id = "h", text = tostring(spr.height) }
  dlg:slider{ id = "variants", label = "Variants:", min = 1, max = 8, value = 4 }
  dlg:button{ text = "Generate", focus = false, onclick = function()
    local d = dlg.data
    dlg:close()
    if d.prompt == "" then app.alert("Prompt is empty.") return end
    D._runJob{
      id = newId(), mode = "generate", prompt = d.prompt,
      target_size = { d.w, d.h }, variants = d.variants, frames = {},
    }
  end }
  dlg:button{ text = "Cancel" }
  dlg:show{ wait = false }
end

-- Export current selection as a white-on-black mask PNG (base64).
function D._exportMask()
  local spr = app.sprite
  local sel = spr.selection
  if sel.isEmpty then return nil end
  local img = Image(spr.width, spr.height)  -- RGBA, all transparent/black
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

function D.edit()
  if not app.sprite then app.alert("Open a sprite first.") return end
  local spr = app.sprite
  local dlg = Dialog("SpriteForge — Edit with AI")
  dlg:entry{ id = "prompt", label = "Prompt:", focus = true }
  dlg:slider{ id = "strength", label = "Strength %:", min = 20, max = 90,
              value = 60 }
  dlg:slider{ id = "variants", label = "Variants:", min = 1, max = 8, value = 4 }
  dlg:button{ text = "Generate", onclick = function()
    local d = dlg.data
    dlg:close()
    if d.prompt == "" then app.alert("Prompt is empty.") return end
    D._runJob{
      id = newId(), mode = "edit", prompt = d.prompt,
      target_size = { spr.width, spr.height },
      variants = d.variants, strength = d.strength / 100,
      frames = { { image = D._exportFrame() } },
    }
  end }
  dlg:button{ text = "Cancel" }
  dlg:show{ wait = false }
end

function D.inpaint()
  if not app.sprite then app.alert("Open a sprite first.") return end
  local spr = app.sprite
  if spr.selection.isEmpty then
    app.alert("Select the region to redraw first (rectangle/lasso).")
    return
  end
  local dlg = Dialog("SpriteForge — Inpaint Selection")
  dlg:entry{ id = "prompt", label = "Prompt:", focus = true }
  dlg:slider{ id = "variants", label = "Variants:", min = 1, max = 8, value = 4 }
  dlg:button{ text = "Generate", onclick = function()
    local d = dlg.data
    local mask = D._exportMask()
    dlg:close()
    if not mask then app.alert("Selection was lost - select the region again.") return end
    if d.prompt == "" then app.alert("Prompt is empty.") return end
    D._runJob{
      id = newId(), mode = "inpaint", prompt = d.prompt,
      target_size = { spr.width, spr.height }, variants = d.variants,
      frames = { { image = D._exportFrame(), mask = mask } },
    }
  end }
  dlg:button{ text = "Cancel" }
  dlg:show{ wait = false }
end

return D

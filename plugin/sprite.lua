-- Sprite/image IO: export the current frame for the server, turn replies
-- back into layers.
local pluginDir = ...
local b64 = dofile(app.fs.joinPath(pluginDir, "base64.lua"))

local S = {}

local function tempPath(name)
  return app.fs.joinPath(app.fs.tempPath, "spriteloom_" .. name)
end

local function readFile(path)
  local f = io.open(path, "rb")
  if not f then return nil end
  local data = f:read("a"); f:close(); return data
end

local function writeFile(path, data)
  local f = assert(io.open(path, "wb")); f:write(data); f:close()
end

function S.newId()
  return tostring(os.time()) .. "-" .. tostring(math.random(10000))
end

-- Flatten current frame to a PNG and return it base64-encoded.
function S.exportFrame()
  local spr = app.sprite
  local img = Image(spr.width, spr.height)
  img:drawSprite(spr, app.frame.frameNumber)
  local p = tempPath("frame.png")
  img:saveAs(p)
  local data = readFile(p)
  if not data then error("could not read temp file: " .. p) end
  return b64.encode(data)
end

-- Current selection as a white-on-black mask PNG (base64), or nil.
function S.exportMask()
  local spr = app.sprite
  local sel = spr.selection
  if sel.isEmpty then return nil end
  local img = Image(spr.width, spr.height)
  local white = Color{ r = 255, g = 255, b = 255 }
  img:clear(Color{ r = 0, g = 0, b = 0 })
  -- only the bounding box can be white; scanning the whole canvas cost
  -- 262144 contains() calls on Aseprite's UI thread
  local b = sel.bounds
  local x0, y0 = math.max(0, b.x), math.max(0, b.y)
  local x1 = math.min(spr.width - 1, b.x + b.width - 1)
  local y1 = math.min(spr.height - 1, b.y + b.height - 1)
  for y = y0, y1 do
    for x = x0, x1 do
      if sel:contains(x, y) then img:drawPixel(x, y, white) end
    end
  end
  local p = tempPath("mask.png")
  img:saveAs(p)
  return b64.encode(readFile(p))
end

-- Server images arrive as raw RGBA bytes and become an in-memory Image.
-- No temp PNG + Image{fromFile}: that spammed Aseprite's Recent Files.
function S.imageFromPayload(spec, n)
  if type(spec) == "string" then  -- older server still sends PNG base64
    local p = tempPath("variant" .. n .. ".png")
    writeFile(p, b64.decode(spec))
    return Image{ fromFile = p }
  end
  local img = Image(spec.w, spec.h, ColorMode.RGB)
  img.bytes = b64.decode(spec.px)
  return img
end

-- {r,g,b} rows of an Aseprite Palette, or nil if empty.
function S.paletteColors(pal)
  if not pal then return nil end
  local colors = {}
  for i = 0, #pal - 1 do
    local c = pal:getColor(i)
    colors[#colors + 1] = { c.red, c.green, c.blue }
  end
  return #colors > 0 and colors or nil
end

-- Only the palette swatches selected in Aseprite's color bar; nil if none.
function S.selectedPalette()
  local spr = app.sprite
  local idx = app.range and app.range.colors
  if not spr or not idx or #idx == 0 then return nil end
  local pal = spr.palettes[1]
  local colors = {}
  for _, i in ipairs(idx) do
    local c = pal:getColor(i)
    colors[#colors + 1] = { c.red, c.green, c.blue }
  end
  return #colors > 0 and colors or nil
end

-- Active sprite's whole palette; nil when no sprite is open.
function S.spritePalette()
  local spr = app.sprite
  return spr and S.paletteColors(spr.palettes[1]) or nil
end

-- Palette loaded from a .gpl/.pal/.png/.aseprite file; nil on failure.
function S.paletteFromFile(path)
  if not path or path == "" then return nil end
  local ok, pal = pcall(function() return Palette{ fromFile = path } end)
  return (ok and pal) and S.paletteColors(pal) or nil
end

function S.insertAsLayer(img, name)
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
  app.transaction("Spriteloom: insert variant", function()
    layer = spr:newLayer()
    layer.name = name
    spr:newCel(layer, app.frame, img, Point(0, 0))
  end)
  app.refresh()
  return { sprite = spr, layer = layer }
end

function S.removeInserted(entry)
  local ok = pcall(function()
    if entry.created then
      entry.sprite:close()  -- closes the untitled sprite without prompting
    else
      app.transaction("Spriteloom: remove variant", function()
        entry.sprite:deleteLayer(entry.layer)
      end)
    end
  end)
  app.refresh()
  return ok
end

-- Toggle a variant in/out of the sprite; returns the new inserted entry.
function S.toggleVariant(inserted, n, img, prefix)
  if inserted[n] then
    S.removeInserted(inserted[n])
    inserted[n] = nil
    return false
  end
  inserted[n] = S.insertAsLayer(img, prefix .. n)
  return true
end

return S

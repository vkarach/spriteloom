-- Theme colors and canvas drawing. Aseprite: dynamic text must live on a
-- canvas, and whatever shrinks/closes a window needs app.refresh() or ghosts.
local U = {}

U.SELECTED = Color{ r = 106, g = 160, b = 100 }

-- Colors come from the active theme so canvases match light and dark.
function U.themeColor(name, fallback)
  local ok, c = pcall(function() return app.theme.color[name] end)
  if ok and c then return c end
  return fallback
end

function U.face()
  return U.themeColor("window_face", Color{ r = 200, g = 200, b = 200 })
end

function U.text()
  return U.themeColor("text", Color{ r = 40, g = 40, b = 40 })
end

function U.shade(c, f)
  return Color{ r = math.floor(c.red * f), g = math.floor(c.green * f),
                b = math.floor(c.blue * f) }
end

-- Checkerboard drawn as one cached Image blit: per-square fillRect cost
-- thousands of calls per repaint and lagged the scroll.
local checkerImg
function U.drawChecker(gc, dx, dy, dw, dh)
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

-- Scale an image into a box, centered, on a checkerboard.
function U.drawThumb(gc, img, bx, by, bw, bh)
  local s = math.min(bw / img.width, bh / img.height)
  local dw = math.max(1, math.floor(img.width * s))
  local dh = math.max(1, math.floor(img.height * s))
  local dx, dy = bx + (bw - dw) // 2, by + (bh - dh) // 2
  U.drawChecker(gc, dx, dy, dw, dh)
  gc:drawImage(img, Rectangle(0, 0, img.width, img.height),
               Rectangle(dx, dy, dw, dh))
end

-- Layout for a click-to-insert variant grid: 1-2 across, then 2, then 4.
function U.gridLayout(imgs, maxW, maxH)
  local count = #imgs
  if count == 0 then return { cols = 1, rows = 1, cw = 1, ch = 1, scale = 1,
                              iw = 1, ih = 1 } end
  local iw, ih = imgs[1].width, imgs[1].height
  local cols = (count <= 2) and count or ((count <= 4) and 2 or 4)
  local rows = math.ceil(count / cols)
  local scale = math.min(maxW / (cols * iw), maxH / (rows * ih))
  if scale >= 1 then scale = math.min(math.floor(scale), 6) end
  return { cols = cols, rows = rows, scale = scale, iw = iw, ih = ih,
           cw = math.floor(iw * scale) + 6, ch = math.floor(ih * scale) + 6 }
end

-- Paint every variant plus the green frame on the inserted ones.
function U.drawVariants(gc, imgs, g, inserted, yOffset)
  yOffset = yOffset or 0
  local dw, dh = math.floor(g.iw * g.scale), math.floor(g.ih * g.scale)
  for n, img in ipairs(imgs) do
    local c = (n - 1) % g.cols
    local r = math.floor((n - 1) / g.cols)
    local dx, dy = c * g.cw + 3, yOffset + r * g.ch + 3
    U.drawChecker(gc, dx, dy, dw, dh)
    gc:drawImage(img, Rectangle(0, 0, g.iw, g.ih), Rectangle(dx, dy, dw, dh))
    if inserted[n] then
      gc.color = U.SELECTED
      gc:fillRect(Rectangle(dx - 2, dy - 2, dw + 4, 2))
      gc:fillRect(Rectangle(dx - 2, dy + dh, dw + 4, 2))
      gc:fillRect(Rectangle(dx - 2, dy, 2, dh))
      gc:fillRect(Rectangle(dx + dw, dy, 2, dh))
    end
  end
end

-- Variant index under the cursor, or nil.
function U.variantAt(ev, g, imgs, yOffset)
  local c = math.floor(ev.x / g.cw)
  local r = math.floor((ev.y - (yOffset or 0)) / g.ch)
  if c < 0 or c >= g.cols or r < 0 then return nil end
  local n = r * g.cols + c + 1
  return imgs[n] and n or nil
end

-- "20260719-013000_..." -> "2026-07-19 01:30"; "" when the name has no stamp.
function U.runWhen(name)
  local y4, mo, dd, hh, mi = name:match("^(%d%d%d%d)(%d%d)(%d%d)%-(%d%d)(%d%d)")
  return y4 and string.format("%s-%s-%s %s:%s", y4, mo, dd, hh, mi) or ""
end

function U.ellipsize(text, limit)
  if #text > limit then return text:sub(1, limit) .. "..." end
  return text
end

-- Word-wrapped read-only view of the exact prompt that will be sent.
function U.showPromptPreview(text)
  local lines, line = {}, ""
  for word in text:gmatch("%S+") do
    if line ~= "" and #line + #word + 1 > 68 then
      lines[#lines + 1] = line
      line = word
    else
      line = (line == "") and word or (line .. " " .. word)
    end
  end
  if line ~= "" then lines[#lines + 1] = line end
  local H = 12 + #lines * 14
  local dlg = Dialog("SpriteForge - Full prompt")
  dlg:canvas{
    width = 440, height = H,
    onpaint = function(ev)
      local gc = ev.context
      gc.color = U.face()
      gc:fillRect(Rectangle(0, 0, 440, H))
      gc.color = U.text()
      for n, l in ipairs(lines) do
        gc:fillText(l, 8, 6 + (n - 1) * 14)
      end
    end,
  }
  dlg:button{ text = "Close" }
  dlg:show{ wait = true }
  app.refresh()
end

return U

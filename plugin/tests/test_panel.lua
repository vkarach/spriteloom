-- Loads every module against a stubbed Aseprite API and drives the panel's
-- painters in each server state. Run from the repo root.

local failures = 0
local function check(what, ok, err)
  if not ok then
    failures = failures + 1
    print(string.format("FAIL %s\n  %s", what, tostring(err)))
  end
end

-- ---------------------------------------------------------------- stubs
local function stubColor(t)
  t = t or {}
  return { red = t.r or 0, green = t.g or 0, blue = t.b or 0 }
end
Color = setmetatable({}, { __call = function(_, t) return stubColor(t) end })
Rectangle = setmetatable({}, { __call = function(_, x, y, w, h)
  return { x = x, y = y, width = w, height = h }
end })
Point = setmetatable({}, { __call = function(_, x, y)
  return { x = x, y = y }
end })
ColorMode = { RGB = 0 }

local function stubImage(w, h)
  local im
  im = {
    width = w or 8, height = h or 8, bytes = "",
    painted = {}, cleared = nil, contains = 0,
    putPixel = function() end,
    drawPixel = function(_, x, y) im.painted[x .. "," .. y] = true end,
    clear = function(_, c) im.cleared = c end,
    drawImage = function() end, drawSprite = function() end,
    saveAs = function() end,
  }
  return im
end
Image = setmetatable({}, { __call = function(_, a, b)
  if type(a) == "table" then return stubImage() end
  return stubImage(a, b)
end })

Timer = nil       -- panel must survive a build without timers
WebSocketMessageType = { OPEN = 0, TEXT = 1, CLOSE = 2 }

-- REPLY is what the server "answers" on connect; nil = silence
REPLY = nil
WebSocket = setmetatable({}, { __call = function(_, t)
  local ws = { close = function() end, sendText = function() end }
  ws.connect = function()
    if not REPLY then return end
    t.onreceive(WebSocketMessageType.OPEN, "")
    t.onreceive(WebSocketMessageType.TEXT, "reply")
  end
  return ws
end })
json = { encode = function() return "{}" end,
         decode = function() return REPLY end }

app = {
  fs = { joinPath = function(...)
           return table.concat({ ... }, "/")
         end,
         tempPath = "/tmp" },
  theme = { color = {} },   -- forces the fallback branch of themeColor
  pixelColor = { rgba = function() return 0 end },
  sprite = nil,
  frame = { frameNumber = 1 },
  refresh = function() end,
  transaction = function(_, fn) if fn then fn() end end,
}

-- Dialog stub: records widgets, exposes .data, and lets tests fire onpaint.
local lastDialog
local function stubDialog()
  local d = { widgets = {}, data = {}, bounds = Rectangle(0, 0, 300, 400) }
  local function add(kind)
    return function(self, t)
      t = t or {}
      d.widgets[#d.widgets + 1] = { kind = kind, spec = t }
      if t.id then
        if t.option ~= nil then d.data[t.id] = t.option
        elseif t.text ~= nil and kind ~= "button" then d.data[t.id] = t.text
        elseif t.value ~= nil then d.data[t.id] = t.value
        elseif t.selected ~= nil then d.data[t.id] = t.selected end
      end
      return self
    end
  end
  d.separator, d.combobox, d.entry = add("separator"), add("combobox"), add("entry")
  d.check, d.number, d.slider = add("check"), add("number"), add("slider")
  d.button, d.canvas = add("button"), add("canvas")
  d.modify = function(self, t)
    if t.text ~= nil and t.id and self.data[t.id] ~= nil then
      self.data[t.id] = t.text
    end
    return self
  end
  d.repaint = function() end
  d.close = function() end
  d.show = function() end
  lastDialog = d
  return d
end
Dialog = setmetatable({}, { __call = function(_, _) return stubDialog() end })

-- Graphics context stub: every call the painters make must exist here.
local function stubGC()
  return {
    color = nil, strokeWidth = 0,
    fillRect = function() end,
    fillText = function() end,
    drawImage = function() end,
    beginPath = function() end,
    moveTo = function() end,
    lineTo = function() end,
    stroke = function() end,
    measureText = function(_, s) return { width = #s * 6, height = 10 } end,
  }
end

-- ---------------------------------------------------------------- tests
local ok, err = pcall(function()
  return assert(loadfile("plugin/ui.lua"))("plugin")
end)
check("ui.lua loads", ok, err)
local ui = ok and err or nil

for _, name in ipairs({ "prompt.lua", "sprite.lua", "results.lua",
                        "history.lua", "dialogs.lua" }) do
  local good, e = pcall(function()
    return assert(loadfile("plugin/" .. name))("plugin")
  end)
  check(name .. " loads", good, e)
end

-- ui helpers used by the painters
if ui then
  check("runWhen parses a run folder name",
        ui.runWhen("20260719-013000_generate_x") == "2026-07-19 01:30",
        ui.runWhen("20260719-013000_generate_x"))
  check("runWhen tolerates a nameless run", ui.runWhen("weird") == "", "")
  check("ellipsize leaves short text alone", ui.ellipsize("abc", 10) == "abc", "")
  check("ellipsize truncates long text",
        ui.ellipsize("abcdefghij", 4) == "abcd...", ui.ellipsize("abcdefghij", 4))

  local g = ui.gridLayout({}, 600, 440)
  check("gridLayout survives zero images", g.cols == 1 and g.ch >= 1, "")
  local imgs = { stubImage(16, 16), stubImage(16, 16), stubImage(16, 16) }
  g = ui.gridLayout(imgs, 600, 440)
  check("gridLayout picks 2 columns for 3 images", g.cols == 2, g.cols)
  check("gridLayout clamps upscaling to 6x", g.scale <= 6, g.scale)
  -- hit testing must agree with the painted geometry
  local hit = ui.variantAt({ x = g.cw + 3, y = 3 }, g, imgs)
  check("variantAt finds the second cell", hit == 2, hit)
  check("variantAt rejects an empty cell",
        ui.variantAt({ x = g.cw * 5, y = 3 }, g, imgs) == nil, "")
  local painted, e2 = pcall(ui.drawVariants, stubGC(), imgs, g, { [1] = true })
  check("drawVariants paints a selected variant", painted, e2)
end

-- exportMask must paint exactly the selection, and must not walk the whole
-- canvas to do it (that ran on Aseprite's UI thread).
do
  local S = assert(loadfile("plugin/sprite.lua"))("plugin")
  local madeImage
  local realImage = Image
  -- saveAs is a stub, so the file the export reads back never exists.
  local realOpen = io.open
  io.open = function()
    return { read = function() return "fake png bytes" end,
             write = function() end, close = function() end }
  end
  Image = setmetatable({}, { __call = function(_, a, b)
    madeImage = stubImage(type(a) == "table" and 8 or a,
                          type(a) == "table" and 8 or b)
    return madeImage
  end })
  local tested = 0
  app.sprite = {
    width = 512, height = 512,
    selection = {
      isEmpty = false,
      bounds = Rectangle(100, 60, 20, 10),
      contains = function(_, x, y)
        tested = tested + 1
        return x >= 100 and x < 120 and y >= 60 and y < 70
      end,
    },
  }
  local ok2, e = pcall(S.exportMask)
  check("exportMask runs", ok2, e)
  check("exportMask fills the canvas black once",
        madeImage and madeImage.cleared ~= nil, "clear() not called")
  check("exportMask only tests the selection bounds (got " .. tested .. ")",
        tested == 20 * 10, tested)
  local painted = 0
  for _ in pairs(madeImage.painted) do painted = painted + 1 end
  check("exportMask whitens exactly the selection", painted == 20 * 10, painted)
  check("exportMask paints inside the selection",
        madeImage.painted["100,60"] == true, "corner missing")
  check("exportMask leaves outside pixels black",
        madeImage.painted["99,60"] == nil, "painted outside")

  -- A selection hanging off the canvas must be clamped, not indexed past it.
  app.sprite.selection.bounds = Rectangle(-5, -5, 10, 10)
  local ok3, e3 = pcall(S.exportMask)
  check("exportMask clamps a selection that starts off-canvas", ok3, e3)

  app.sprite.selection.isEmpty = true
  check("exportMask returns nil without a selection", S.exportMask() == nil, "")
  Image, io.open = realImage, realOpen
  app.sprite = nil
end

-- State lives in closures, so the reply at open time selects the branch.
local D = assert(loadfile("plugin/dialogs.lua"))("plugin")

local scenarios = {
  { name = "checking (no reply)", reply = nil },
  { name = "online", reply = { type = "pong", model = "ready" } },
  { name = "warming", reply = { type = "pong", model = "loading",
                                progress = 0.42, stage = "Reading files" } },
  -- a load that reports no stage label yet must still paint a bar
  { name = "warming without a stage label",
    reply = { type = "pong", model = "loading" } },
}

for _, sc in ipairs(scenarios) do
  REPLY = sc.reply
  D._isOpen = false           -- reopen for a fresh set of closures
  local opened, e3 = pcall(D.open)
  check("panel opens: " .. sc.name, opened, e3)

  local canvas
  for _, w in ipairs(lastDialog and lastDialog.widgets or {}) do
    if w.kind == "canvas" and w.spec.id == "view" then canvas = w.spec end
  end
  check("panel has a status canvas: " .. sc.name, canvas ~= nil, "")

  if canvas then
    for _, mode in ipairs({ "Generate", "Edit with AI", "Inpaint Selection",
                            "Rotate / Instruct" }) do
      lastDialog.data.mode = mode
      local good, e4 = pcall(canvas.onpaint, { context = stubGC() })
      check(sc.name .. " paints in mode " .. mode, good, e4)
      local clicked, e5 = pcall(canvas.onmouseup, { x = 50, y = 20 })
      check(sc.name .. " handles a click in mode " .. mode, clicked, e5)
    end
  end
end

-- A cleared Width field arrives as "" or nil; the checklist must handle that
-- rather than doing arithmetic on it.
REPLY = { type = "pong", model = "ready" }
D._isOpen = false
pcall(D.open)
for _, w in ipairs(lastDialog and lastDialog.widgets or {}) do
  if w.kind == "canvas" and w.spec.id == "view" then
    for _, size in ipairs({ 0, "", "abc" }) do
      lastDialog.data.mode = "Generate"
      lastDialog.data.genSubject = "a sword"
      lastDialog.data.w, lastDialog.data.h = size, size
      local good, e = pcall(w.spec.onpaint, { context = stubGC() })
      check("checklist paints with width = " .. tostring(size), good, e)
    end
    lastDialog.data.w, lastDialog.data.h = nil, nil
    local good, e = pcall(w.spec.onpaint, { context = stubGC() })
    check("checklist paints with a missing width", good, e)
  end
end

-- With a sprite open the checklist takes its other branch (requirements met).
REPLY = { type = "pong", model = "ready" }
app.sprite = { width = 32, height = 32,
               selection = { isEmpty = false,
                             contains = function() return true end } }
D._isOpen = false
local withSprite, e6 = pcall(D.open)
check("panel opens with a sprite", withSprite, e6)
for _, w in ipairs(lastDialog and lastDialog.widgets or {}) do
  if w.kind == "canvas" and w.spec.id == "view" then
    lastDialog.data.mode = "Inpaint Selection"
    lastDialog.data.prompt = "a red roof"
    local good, e7 = pcall(w.spec.onpaint, { context = stubGC() })
    check("checklist paints with all requirements met", good, e7)
  end
end

if failures == 0 then
  print("panel: all tests passed")
else
  print(failures .. " failure(s)")
  os.exit(1)
end

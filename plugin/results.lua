-- Variant viewer shared by fresh results and history runs: a content-sized
-- grid with a seed footer, click a variant to insert/remove.
local pluginDir = ...
local ui = dofile(app.fs.joinPath(pluginDir, "ui.lua"))
local sprite = assert(loadfile(
  app.fs.joinPath(pluginDir, "sprite.lua")))(pluginDir)

local R = {}

local FOOT = 24     -- seed strip height, drawn at the bottom of the canvas
local MINW = 264    -- keep the footer/header text readable for a lone variant

-- opts: title, imgs, seeds, prefix, onInserted, headers (dim text lines above
-- the grid), onBack. Sizing the canvas to the grid (not a fixed width) keeps
-- the window snug for 1/4/8 and stops Aseprite stretching it to fill.
function R.showGrid(opts)
  local imgs, seeds = opts.imgs, opts.seeds
  local headers = opts.headers
  local HEAD = headers and (#headers * 14 + 6) or 0
  local g = ui.gridLayout(imgs, 600, 440)
  local W = math.max(g.cols * g.cw, MINW)
  local H = HEAD + g.rows * g.ch
  local inserted, curSeed = {}, ""

  local dlg = Dialog(opts.title)
  dlg:canvas{
    id = "grid", width = W, height = H + FOOT,
    onpaint = function(ev)
      local gc = ev.context
      gc.color = ui.face()
      gc:fillRect(Rectangle(0, 0, W, H + FOOT))
      if headers then
        for i, h in ipairs(headers) do
          gc.color = h.dim and ui.shade(ui.text(), 0.75) or ui.text()
          gc:fillText(ui.ellipsize(h.text, (W - 12) // 6), 6, 4 + (i - 1) * 14)
        end
      end
      ui.drawVariants(gc, imgs, g, inserted, HEAD)
      ui.drawSeedBar(gc, curSeed, 0, H, W, FOOT)
    end,
    onmouseup = function(ev)
      local n = ui.variantAt(ev, g, imgs, HEAD)
      if not n then return end
      local added = sprite.toggleVariant(inserted, n, imgs[n], opts.prefix)
      if opts.onInserted then opts.onInserted(n, added) end
      curSeed = ui.seedText(seeds, n)
      dlg:modify{ id = "copyseed", text = "Copy seed" }
      dlg:repaint()
    end,
  }
  -- Copy seed sits just left of Close (the primary post-pick action); Close
  -- stays last, matching the main panel and History.
  if opts.onBack then
    dlg:button{ text = "< Back", onclick = function()
      dlg:close()
      opts.onBack()
    end }
  end
  dlg:button{ id = "copyseed", text = "Copy seed", onclick = function()
    if curSeed ~= "" and ui.copyText(curSeed) then
      dlg:modify{ id = "copyseed", text = "Copied" }
    end
  end }
  dlg:button{ text = "Close" }
  dlg:show{ wait = true }
  app.refresh()
end

function R.showResults(imgs, seeds, onInserted)
  R.showGrid{
    title = "Spriteloom - Results (click a variant to insert)",
    imgs = imgs, seeds = seeds, prefix = "Spriteloom ",
    onInserted = onInserted,
  }
end

return R

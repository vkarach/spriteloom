-- SpriteForge control panel. Results, history, prompt assembly, theme
-- drawing and sprite IO live in their own modules.
--
-- Hard-learned rules for Aseprite dialogs:
--  * dynamic text lives on a canvas (repaint does not relayout the dialog);
--  * after anything that can shrink/close a window, call app.refresh() to
--    repaint the vacated screen area, or a ghost strip stays behind.
local pluginDir = ...
-- Modules take pluginDir as their argument, so they can load each other.
local function moduleFrom(name)
  return assert(loadfile(app.fs.joinPath(pluginDir, name)))(pluginDir)
end

local client = dofile(app.fs.joinPath(pluginDir, "client.lua"))
local P = moduleFrom("prompt.lua")
local ui = moduleFrom("ui.lua")
local sprite = moduleFrom("sprite.lua")
local results = moduleFrom("results.lua")
local history = moduleFrom("history.lua")

local D = {}

-- Settings survive across reopenings of the panel.
local last = { mode = "Generate", prompt = "", w = nil, h = nil,
               variants = 4, background = "Auto",
               view = "Side view (right)", subject = "character",
               instruction = "", symmetry = false,
               genView = "3/4 view", genSubject = "", genDetails = "" }

local STATUS_W, STATUS_H = 282, 58  -- status line + up to 3 checklist rows

function D.open()
  if D._isOpen then return end

  local state = "idle"        -- idle | running | done | error
  local statusText = "Set the parameters and press Run."
  local progress = 0
  local job = nil
  local serverStatus = "checking"  -- checking | online | warming | offline
  local loadProgress = 0    -- 0..1 fraction of the current load stage
  local loadStage = nil     -- label of the current load stage
  local pingBusy = false
  local pingAt = 0          -- watchdog: never let a lost ping jam the loop
  local pingMisses = 0      -- debounce: one lost ping is not "offline"
  local pingTimer
  local pingInterval = 3.0
  -- forward declarations: checkServer runs before these are defined
  local updateHint, retunePing, syncButtons
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
      function(model, loadFrac, stage)
        pingBusy = false
        pingMisses = 0
        -- server preloads Klein at startup; show it until the model is in
        serverStatus = (model == "loading") and "warming" or "online"
        loadProgress = loadFrac or 0
        loadStage = stage
        -- a stale "Server offline" error must clear once the server answers
        if state == "error" and statusText:find("offline") then
          updateHint()
        end
        retunePing()
        syncButtons()
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
        syncButtons()
        repaint()
      end)
  end

  -- Warming wants a lively bar (1s pings); 3s otherwise keeps the
  -- online/offline word honest without spamming.
  function retunePing()
    if not Timer or not pingTimer then return end
    local want = (serverStatus == "warming") and 1.0 or 3.0
    if pingInterval == want then return end
    pingInterval = want
    pingTimer:stop()
    pingTimer = Timer{ interval = want, ontick = checkServer }
    pingTimer:start()
  end

  -- Buttons follow both the job state and the server state: no point
  -- clicking Run or History at a dead server.
  function syncButtons()
    local up = serverStatus == "online" or serverStatus == "warming"
    dlg:modify{ id = "run", enabled = up and state ~= "running" }
    dlg:modify{ id = "historybtn", enabled = up }
    dlg:modify{ id = "cancel", enabled = state == "running" }
  end

  local function setState(s, text)
    state = s
    statusText = text
    local running = (s == "running")
    syncButtons()
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
    -- third field = the untruncated text, expanded by a click on the row
    local function willSend(text)
      if #text > 34 then
        return { true, 'Will send: "' .. text:sub(1, 34) .. '..."', text }
      end
      return { true, 'Will send: "' .. text .. '"' }
    end
    if m == "Generate" then
      local text = P.assembleGenPrompt(d.genView, d.genSubject, d.genDetails)
      reqs[1] = text and willSend(text)
        or { false, "Subject describes what to generate" }
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
      local text = P.assembleInstruction(d.viewPreset, d.subject, d.instruction)
      reqs[2] = text and willSend(text)
        or { false, "Pick a view preset or type an instruction" }
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

    local mode = P.MODE_KEY[d.mode]
    local payload = { id = sprite.newId(), mode = mode,
                      variants = d.variants, frames = {},
                      background = P.BG_KEY[d.background] or "auto" }
    if mode == "instruct" then
      local spr = app.sprite
      if not spr then
        setState("error", "Open a sprite first.")
        return
      end
      local instruction = P.assembleInstruction(d.viewPreset, d.subject,
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
      payload.frames = { { image = sprite.exportFrame() } }
    elseif mode == "generate" then
      local text = P.assembleGenPrompt(d.genView, d.genSubject, d.genDetails)
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
        payload.frames = { { image = sprite.exportFrame() } }
      else
        local mask = sprite.exportMask()
        if not mask then
          setState("error", "No selection. Select the region to redraw first.")
          return
        end
        payload.frames = { { image = sprite.exportFrame(), mask = mask } }
      end
    end

    progress = 0
    -- during model load the job queues on the server; say so instead of a
    -- misleading endless "Contacting server" (Cancel works while queued)
    setState("running", serverStatus == "warming"
             and "Queued - will start when the model is loaded"
             or "Contacting server...")

    job = client.request(payload, {
      onprogress = function(v, stage)
        -- A stage message (model load / decode / postprocess) only relabels;
        -- the bar stays where it is. Numeric ticks fill the bar.
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
        for n, s in ipairs(images) do
          imgs[n] = sprite.imageFromPayload(s, n)
        end
        setState("done", string.format(
          "%d variants ready. Press Run for more.", #imgs))
        results.showResults(imgs, function(n, added)
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

  -- The status canvas: one status line, a server dot, and below it either
  -- a progress bar or the requirements checklist.
  local function paintBar(gc, face, frac, label)
    local bx, by, bw, bh = 8, 22, STATUS_W - 16, 10
    gc.color = ui.shade(face, 0.85)
    gc:fillRect(Rectangle(bx, by, bw, bh))
    gc.color = ui.shade(face, 0.55)
    gc:fillRect(Rectangle(bx + 1, by + 1,
                          math.floor((bw - 2) * frac), bh - 2))
    gc.color = ui.shade(ui.text(), 0.75)
    if label then gc:fillText(label, bx, 38) end
    return bx, by, bw, bh
  end

  local function paintChecklist(gc, face)
    local textCol = ui.text()
    local guide = ui.shade(face, 0.70)
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
        gc.color = ui.SELECTED
        gc:fillRect(Rectangle(26, y, 9, 9))
        -- stroked tick; without the path API the green box still reads "done"
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
        gc.color = ui.shade(face, 0.60)
        gc:fillRect(Rectangle(26, y, 9, 9))
        gc.color = ui.shade(face, 0.93)
        gc:fillRect(Rectangle(27, y + 1, 7, 7))
      end
      gc.color = r[1] and ui.shade(textCol, 0.85) or textCol
      gc:fillText(r[2], 40, y + 2)
    end
  end

  local SRV = { online   = { Color{ r = 106, g = 160, b = 100 }, "online" },
                warming  = { Color{ r = 214, g = 138, b = 48 }, "loading" },
                offline  = { Color{ r = 168, g = 82, b = 62 }, "offline" },
                checking = { Color{ r = 214, g = 138, b = 48 }, "checking" } }

  local function paintStatus(ev)
    local gc = ev.context
    local face = ui.face()
    -- Inset panel: slightly darker than the dialog with a border, so the
    -- text sits in a visible container instead of floating bare.
    gc.color = ui.shade(face, 0.72)
    gc:fillRect(Rectangle(0, 0, STATUS_W, STATUS_H))
    gc.color = ui.shade(face, 0.93)
    gc:fillRect(Rectangle(1, 1, STATUS_W - 2, STATUS_H - 2))
    if state == "error"
       or (state ~= "running" and serverStatus == "offline") then
      gc.color = Color{ r = 168, g = 82, b = 62 }
    else
      gc.color = ui.text()
    end
    -- One story at a time. Until the server is ready the status line talks
    -- ONLY about the server; task hints/checklist wait for online.
    local line = statusText
    if state == "running" then
      line = line:gsub("%.+$", "")
      line = line .. string.rep(".", math.floor(os.clock() * 3) % 4)
    elseif serverStatus == "checking" then
      line = "Connecting to server..."
    elseif serverStatus == "offline" then
      line = "Server offline - run start-server.bat"
    elseif serverStatus == "warming" and state ~= "error" then
      line = "Loading Klein model"
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

    local dot, word = SRV[serverStatus][1], SRV[serverStatus][2]
    local wordW = 6 * #word
    local ok, size = pcall(function() return gc:measureText(word) end)
    if ok and size then wordW = size.width end
    gc.color = dot
    gc:fillRect(Rectangle(STATUS_W - 14, 7, 6, 6))
    gc.color = ui.shade(ui.text(), 0.8)
    gc:fillText(word, STATUS_W - 18 - wordW, 6)

    local loadLabel = string.format("%s  %d%%", loadStage or "Starting",
                                    math.floor(loadProgress * 100))
    if state == "running" then
      if progress > 0 then
        paintBar(gc, face, progress, nil)
        gc.color = ui.shade(ui.text(), 0.75)
        gc:fillText(string.format("%d%%", math.floor(progress * 100)),
                    math.floor(STATUS_W / 2) - 8, 38)
      elseif serverStatus == "warming" then
        paintBar(gc, face, loadProgress, loadLabel)  -- queued behind the load
      else
        -- contacting the server: a sweeping segment, nothing to measure yet
        local bx, by, bw, bh = paintBar(gc, face, 0, nil)
        local seg = math.floor((bw - 2) * 0.25)
        local ph = (os.clock() * 0.8) % 1
        local tri = ph < 0.5 and (ph * 2) or (2 - ph * 2)
        gc.color = ui.shade(face, 0.55)
        gc:fillRect(Rectangle(bx + 1 + math.floor((bw - 2 - seg) * tri),
                              by + 1, seg, bh - 2))
      end
    elseif serverStatus == "warming" then
      -- exactly what the server console shows: the bar fills for the
      -- current stage and resets on the next one
      paintBar(gc, face, loadProgress, loadLabel)
    elseif serverStatus ~= "checking" and serverStatus ~= "offline" then
      paintChecklist(gc, face)  -- offline/checking: the line says it all
    end
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
                options = P.GEN_VIEW_ORDER, onchange = updateHint,
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
                options = P.PRESET_ORDER, onchange = updateHint,
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
    onpaint = paintStatus,
    onmouseup = function(ev)
      -- checklist rows with a stored full prompt expand on click
      if state == "running" or serverStatus ~= "online" then return end
      local i = math.floor((ev.y - 16) / 13) + 1
      local r = requirements()[i]
      if r and r[3] then ui.showPromptPreview(r[3]) end
    end,
  }
  dlg:button{ id = "run", text = "Run", onclick = startRun }
  dlg:button{ id = "cancel", text = "Cancel", enabled = false,
              onclick = function()
    if job then job.cancel() end
    setState("idle", "Cancelled. Press Run to try again.")
  end }
  dlg:button{ id = "historybtn", text = "History", onclick = function()
    history.showHistory(client)
  end }
  dlg:button{ id = "closebtn", text = "Close", onclick = function()
    dlg:close()
  end }

  D._isOpen = true
  updateHint()
  if Timer then
    pingTimer = Timer{ interval = pingInterval, ontick = checkServer }
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

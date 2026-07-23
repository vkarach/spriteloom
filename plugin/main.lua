local pluginDir

function init(plugin)
  pluginDir = plugin.path

  local chunk = assert(loadfile(app.fs.joinPath(pluginDir, "dialogs.lua")))
  local dialogs = chunk(pluginDir)

  plugin:newCommand{
    id = "SpriteloomOpen",
    title = "Spriteloom...",
    group = "sprite_properties",
    onclick = function() dialogs.open() end,
  }
end

function exit(plugin) end

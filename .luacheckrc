-- Aseprite injects these; luacheck would otherwise call every one undefined.
std = "lua54"
globals = { "app" }
read_globals = {
  "Dialog", "Image", "ImageSpec", "Sprite", "Palette", "Color", "ColorMode",
  "Point", "Rectangle", "Size", "Selection", "Layer", "Cel", "Tag",
  "BlendMode", "GraphicsContext", "MouseButton", "KeyModifier",
  "WebSocket", "WebSocketMessageType", "Timer", "json",
}
-- init/exit are the entry points Aseprite calls; exit's argument is part of
-- the required signature even though the plugin ignores it.
files["plugin/main.lua"] = { globals = { "init", "exit" }, unused_args = false }
-- The harness replaces the Aseprite API (and io.open) with stubs on purpose.
files["plugin/tests/*.lua"] = {
  globals = { "app", "Color", "Rectangle", "Point", "Image", "ColorMode",
              "Dialog", "Timer", "WebSocket", "WebSocketMessageType", "json",
              "REPLY", "io" },
}
max_line_length = 100

-- Cherry OS settings for Hyprland. To change them, copy this file to ~/.config/hypr/hyprland.lua and edit
-- that copy (Hyprland then reads yours instead). Super+/ lists every shortcut.

local cherry = "/usr/lib/cherry/hyprland/cherry-hyprland"

hl.monitor({ output = "", mode = "preferred", position = "auto", scale = "auto" })

hl.on("hyprland.start", function()
    hl.exec_cmd(cherry .. " start")
end)

hl.env("XCURSOR_SIZE", "24")
hl.env("HYPRCURSOR_SIZE", "24")
hl.env("QT_QPA_PLATFORM", "wayland;xcb")
hl.env("QT_QPA_PLATFORMTHEME", "kde")

-- The keyboard layout chosen when Cherry OS was installed.
local function keyboard()
    local kb = { layout = "us", variant = "", model = "", options = "" }
    local f = io.open("/etc/default/keyboard")
    if f then
        for line in f:lines() do
            local key, value = line:match('^XKB(%u+)="?([^"]*)"?')
            if key and kb[key:lower()] ~= nil then kb[key:lower()] = value end
        end
        f:close()
    end
    return kb
end
local kb = keyboard()

hl.config({
    general = {
        gaps_in = 5,
        gaps_out = 12,
        border_size = 2,
        col = {
            active_border = { colors = { "rgba(e8204fee)", "rgba(ff8aa5ee)" }, angle = 45 },
            inactive_border = "rgba(4a2238aa)",
        },
        resize_on_border = true,
        layout = "dwindle",
    },
    decoration = {
        rounding = 10,
        rounding_power = 2,
        shadow = { enabled = true, range = 6, render_power = 3, color = 0xee1a0a12 },
        blur = { enabled = true, size = 4, passes = 2, vibrancy = 0.17 },
    },
    animations = { enabled = true },
    dwindle = { preserve_split = true },
    misc = {
        force_default_wallpaper = 0,
        disable_hyprland_logo = true,
    },
    input = {
        kb_layout = kb.layout,
        kb_variant = kb.variant,
        kb_model = kb.model,
        kb_options = kb.options,
        follow_mouse = 1,
        touchpad = { natural_scroll = true, tap_to_click = true },
    },
})

hl.curve("easeOutQuint", { type = "bezier", points = { { 0.23, 1 }, { 0.32, 1 } } })
hl.curve("almostLinear", { type = "bezier", points = { { 0.5, 0.5 }, { 0.75, 1 } } })
hl.curve("easy", { type = "spring", mass = 1, stiffness = 71.2633, dampening = 15.8273644 })
hl.animation({ leaf = "global", enabled = true, speed = 10, bezier = "default" })
hl.animation({ leaf = "border", enabled = true, speed = 5.39, bezier = "easeOutQuint" })
hl.animation({ leaf = "windows", enabled = true, speed = 4.79, spring = "easy" })
hl.animation({ leaf = "windowsIn", enabled = true, speed = 4.1, spring = "easy", style = "popin 87%" })
hl.animation({ leaf = "fade", enabled = true, speed = 3.03, bezier = "almostLinear" })
hl.animation({ leaf = "workspaces", enabled = true, speed = 1.94, bezier = "almostLinear", style = "slide" })

hl.gesture({ fingers = 3, direction = "horizontal", action = "workspace" })

-- Shortcuts. Super is the Windows key. Keep desktop/hyprland/shortcuts.txt in step with these.
local mod = "SUPER"
hl.bind(mod .. " + RETURN", hl.dsp.exec_cmd("kitty"))
hl.bind(mod .. " + SPACE", hl.dsp.exec_cmd(cherry .. " apps"))
hl.bind(mod .. " + E", hl.dsp.exec_cmd("nautilus --new-window"))
hl.bind(mod .. " + B", hl.dsp.exec_cmd(cherry .. " browser"))
hl.bind(mod .. " + I", hl.dsp.exec_cmd("cherry-center"))
hl.bind(mod .. " + Q", hl.dsp.window.close())
hl.bind(mod .. " + F", hl.dsp.window.fullscreen())
hl.bind(mod .. " + V", hl.dsp.window.float({ action = "toggle" }))
hl.bind(mod .. " + J", hl.dsp.layout("togglesplit"))
hl.bind(mod .. " + L", hl.dsp.exec_cmd("loginctl lock-session"))
hl.bind(mod .. " + ESCAPE", hl.dsp.exec_cmd(cherry .. " power"))
hl.bind(mod .. " + SLASH", hl.dsp.exec_cmd(cherry .. " shortcuts"))
hl.bind("PRINT", hl.dsp.exec_cmd(cherry .. " screenshot area"))
hl.bind("SHIFT + PRINT", hl.dsp.exec_cmd(cherry .. " screenshot screen"))

for _, dir in ipairs({ "left", "right", "up", "down" }) do
    hl.bind(mod .. " + " .. dir, hl.dsp.focus({ direction = dir }))
    hl.bind(mod .. " + SHIFT + " .. dir, hl.dsp.window.move({ direction = dir }))
end
for i = 1, 10 do
    local key = i % 10
    hl.bind(mod .. " + " .. key, hl.dsp.focus({ workspace = i }))
    hl.bind(mod .. " + SHIFT + " .. key, hl.dsp.window.move({ workspace = i }))
end
hl.bind(mod .. " + mouse_down", hl.dsp.focus({ workspace = "e+1" }))
hl.bind(mod .. " + mouse_up", hl.dsp.focus({ workspace = "e-1" }))
hl.bind(mod .. " + mouse:272", hl.dsp.window.drag(), { mouse = true })
hl.bind(mod .. " + mouse:273", hl.dsp.window.resize(), { mouse = true })

hl.bind("XF86AudioRaiseVolume", hl.dsp.exec_cmd("wpctl set-volume -l 1 @DEFAULT_AUDIO_SINK@ 5%+"), { locked = true, repeating = true })
hl.bind("XF86AudioLowerVolume", hl.dsp.exec_cmd("wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%-"), { locked = true, repeating = true })
hl.bind("XF86AudioMute", hl.dsp.exec_cmd("wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle"), { locked = true })
hl.bind("XF86AudioMicMute", hl.dsp.exec_cmd("wpctl set-mute @DEFAULT_AUDIO_SOURCE@ toggle"), { locked = true })
hl.bind("XF86MonBrightnessUp", hl.dsp.exec_cmd("brightnessctl -e4 -n2 set 5%+"), { locked = true, repeating = true })
hl.bind("XF86MonBrightnessDown", hl.dsp.exec_cmd("brightnessctl -e4 -n2 set 5%-"), { locked = true, repeating = true })
hl.bind("XF86AudioNext", hl.dsp.exec_cmd("playerctl next"), { locked = true })
hl.bind("XF86AudioPrev", hl.dsp.exec_cmd("playerctl previous"), { locked = true })
hl.bind("XF86AudioPlay", hl.dsp.exec_cmd("playerctl play-pause"), { locked = true })
hl.bind("XF86AudioPause", hl.dsp.exec_cmd("playerctl play-pause"), { locked = true })

-- Small dialogs float instead of filling half the screen.
hl.window_rule({
    name = "cherry-floating-tools",
    match = { class = "^(pavucontrol|org.pulseaudio.pavucontrol|nm-connection-editor|blueman-manager|org.gnome.Calculator|kitty-float)$" },
    float = true,
})
-- KDE's screen-sharing helper for X11 apps starts with every login; KDE hides its window itself.
hl.window_rule({
    name = "hide-xwaylandvideobridge",
    match = { class = "^(xwaylandvideobridge)$" },
    float = true,
    max_size = "1 1",
    opacity = "0.0 override",
    no_anim = true,
    no_blur = true,
    no_focus = true,
    no_initial_focus = true,
})
hl.window_rule({
    name = "fix-xwayland-drags",
    match = { class = "^$", title = "^$", xwayland = true, float = true, fullscreen = false, pin = false },
    no_focus = true,
})

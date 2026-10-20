-- live-translate.lua: press Ctrl+T in mpv to generate translated subtitles just ahead of
-- the playhead. The work happens in the `live-translate` Python command; this script only
-- starts and stops it. https://github.com/kastro5/mpv-live-translate
--
-- Options go in script-opts/live_translate.conf (written by `live-translate install-mpv-script`):
--   executable=C:\Users\me\.local\bin\live-translate.exe
--   ffmpeg=C:\ffmpeg\bin\ffmpeg.exe
--   key=ctrl+t
--   debug=no          (yes: same as debug = true in live-translate's config.toml)
--   debug_key=ctrl+T  (Ctrl+Shift+T shows/hides the debug panel while running)

local mp = require "mp"
local utils = require "mp.utils"
local options = require "mp.options"

local opts = { executable = "live-translate", ffmpeg = "", key = "ctrl+t", debug = false, debug_key = "ctrl+T" }
options.read_options(opts, "live_translate")

local STOP_MESSAGE = "live-translate-stop"
local DEBUG_MESSAGE = "live-translate-debug"
local STOP_TIMEOUT_S = 3
local is_windows = package.config:sub(1, 1) == "\\"

local job = nil
local stopping = false

local function ipc_path()
    -- Reuse an existing IPC server (other tools may rely on it); otherwise open one
    local current = mp.get_property("input-ipc-server", "")
    if current ~= "" then
        return current
    end
    local path
    if is_windows then
        path = "\\\\.\\pipe\\mlt-" .. utils.getpid()
    else
        path = "/tmp/mlt-" .. utils.getpid() .. ".sock" -- short: macOS caps socket paths at ~104 bytes
    end
    mp.set_property("input-ipc-server", path)
    return path
end

local function last_line(text)
    local last = nil
    for line in (text or ""):gmatch("[^\r\n]+") do
        last = line
    end
    return last
end

local function start()
    local path = mp.get_property("path")
    local scheme = path and path:match("^(%a[%w+.-]*)://")
    if not path or (scheme and scheme:lower() ~= "http" and scheme:lower() ~= "https") then
        mp.osd_message("live-translate: only local files and direct http(s) links are supported")
        return
    end
    if not scheme then
        path = utils.join_path(mp.get_property("working-directory", ""), path)
    end

    local args = { opts.executable, "serve", "--ipc", ipc_path(), "--file", path }
    if opts.ffmpeg ~= "" then
        table.insert(args, "--ffmpeg")
        table.insert(args, opts.ffmpeg)
    end
    if opts.debug then
        table.insert(args, "--debug")
    end

    stopping = false
    mp.osd_message("live-translate: starting...")
    job = mp.command_native_async({
        name = "subprocess",
        args = args,
        playback_only = false, -- we stop it ourselves so finished work gets cached first
        capture_stdout = true,
        capture_stderr = true,
    }, function(success, result, err)
        job = nil
        if stopping then
            return
        end
        if not success or result.status ~= 0 then
            local reason = last_line(result and result.stderr) or err or result.error_string or "unknown error"
            if result and result.error_string == "init" then
                reason = "could not run '" .. opts.executable .. "'. Run `live-translate install-mpv-script`."
            end
            mp.osd_message("live-translate: " .. reason, 8)
            mp.msg.error(reason)
        end
    end)
end

local function stop()
    if not job then
        return
    end
    stopping = true
    mp.commandv("script-message", STOP_MESSAGE)
    local this_job = job
    mp.add_timeout(STOP_TIMEOUT_S, function()
        if job == this_job then
            mp.abort_async_command(this_job)
            job = nil
        end
    end)
end

mp.add_key_binding(opts.key, "toggle", function()
    if job and not stopping then
        stop()
    elseif not job then
        start()
    end
end)

mp.add_key_binding(opts.debug_key, "debug", function()
    if job and not stopping then
        mp.commandv("script-message", DEBUG_MESSAGE)
    else
        mp.osd_message("live-translate: not running (press " .. opts.key .. " to start)")
    end
end)

mp.register_event("end-file", stop)
mp.register_event("shutdown", stop)

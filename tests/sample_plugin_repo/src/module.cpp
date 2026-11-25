// SPDX-License-Identifier: MIT
//
// Sparxie 插件示例：只负责启动游戏（不注入、不改任何东西）。从这里开始写你的插件。
//
// 改插件时要同步改三处，构建时会自动核对前两处是否一致：
//   1. 下面的 kId / kVersion / kExe / kCaps
//   2. 根目录的 plugin.json（id、version、exeNames、caps）
//   3. 设置项：plugin.json 的 settingsSchema 声明界面，这里读宿主传来的 settings_json
//
// ABI 规则见 third_party/sparxie-plugin-abi/sparxie_game_module.h：纯 C 边界，不让异常越过边界，
// 同一会话的调用都在宿主的同一个专用线程上，release 必须幂等。宿主不会调用 FreeLibrary。
#include <windows.h>

#include <cwchar>
#include <new>
#include <string>

#include "sparxie_game_module.h"

namespace {

constexpr wchar_t kId[] = L"ext.example.game";
constexpr wchar_t kName[] = L"示例游戏";
constexpr wchar_t kVersion[] = L"0.1.0";
constexpr wchar_t kExe[] = L"Game.exe";
constexpr uint32_t kCaps = SPX_CAP_CREATES_PROCESS;

template <size_t N>
constexpr SpxStr Str(const wchar_t (&text)[N]) {
    return SpxStr{text, static_cast<uint32_t>(N - 1)};
}

struct Session {
    std::wstring exe;
    bool windowed = false;
    HANDLE job = nullptr;      // 宿主的 Job（不归我们关闭）
    HANDLE process = nullptr;  // 我们创建的游戏进程
};

SpxError Fail(SpxResult* result, SpxError code, const wchar_t* message, uint32_t stage = 0, uint32_t detail = 0) {
    if (result != nullptr) {
        result->error_code = code;
        result->stage = stage;
        result->detail = detail;
        wcsncpy_s(result->message, 512, message, _TRUNCATE);
    }
    return code;
}

std::wstring ToString(SpxStr value) {
    return value.p != nullptr ? std::wstring(value.p, value.chars) : std::wstring();
}

SpxError GetInfo(const SpxModuleInfo** out);

SpxError CreateSession(const SpxLaunchRequest* request, SpxResult* result, void** session) {
    if (request == nullptr || session == nullptr) {
        return Fail(result, SPX_ERR_INVALID_ARGUMENT, L"参数为空");
    }
    try {
        auto* created = new Session();
        created->exe = ToString(request->exe_path);
        // settings_json 已经由宿主按 plugin.json 的 settingsSchema 校验并补齐默认值，是紧凑 JSON。
        // 设置项多了以后换成真正的 JSON 解析库；示例只有一个开关。
        created->windowed = ToString(request->settings_json).find(L"\"windowed\":true") != std::wstring::npos;
        *session = created;
        return SPX_OK;
    } catch (...) {
        return Fail(result, SPX_ERR_INTERNAL, L"创建会话失败");
    }
}

SpxError PrepareLaunch(void* handle, SpxResult* result) {
    auto* session = static_cast<Session*>(handle);
    if (session == nullptr) {
        return Fail(result, SPX_ERR_INVALID_ARGUMENT, L"会话为空");
    }
    if (GetFileAttributesW(session->exe.c_str()) == INVALID_FILE_ATTRIBUTES) {
        return Fail(result, SPX_ERR_PROCESS_NOT_FOUND, L"找不到游戏 EXE", 1, GetLastError());
    }
    return SPX_OK;
}

SpxError AttachJob(void* handle, void* job, SpxResult* result) {
    auto* session = static_cast<Session*>(handle);
    if (session == nullptr) {
        return Fail(result, SPX_ERR_INVALID_ARGUMENT, L"会话为空");
    }
    session->job = static_cast<HANDLE>(job);
    return SPX_OK;
}

// 声明了 createsProcess：game_pid 为 0，由模块创建进程，并在恢复运行之前放进宿主的 Job。
SpxError Install(void* handle, uint32_t, SpxResult* result) {
    auto* session = static_cast<Session*>(handle);
    if (session == nullptr) {
        return Fail(result, SPX_ERR_INVALID_ARGUMENT, L"会话为空");
    }
    try {
        std::wstring command = L"\"" + session->exe + L"\"";
        if (session->windowed) {
            command += L" -screen-fullscreen 0";
        }
        const size_t slash = session->exe.find_last_of(L"\\/");
        const std::wstring directory = slash == std::wstring::npos ? std::wstring() : session->exe.substr(0, slash);

        STARTUPINFOW startup{};
        startup.cb = sizeof(startup);
        PROCESS_INFORMATION process{};
        if (!CreateProcessW(session->exe.c_str(), command.data(), nullptr, nullptr, FALSE, CREATE_SUSPENDED, nullptr,
                            directory.empty() ? nullptr : directory.c_str(), &startup, &process)) {
            return Fail(result, SPX_ERR_INTERNAL, L"启动游戏失败", 2, GetLastError());
        }
        if (session->job != nullptr && !AssignProcessToJobObject(session->job, process.hProcess)) {
            const DWORD error = GetLastError();
            TerminateProcess(process.hProcess, 1);
            CloseHandle(process.hThread);
            CloseHandle(process.hProcess);
            return Fail(result, SPX_ERR_INTERNAL, L"无法把游戏进程放进宿主的 Job", 3, error);
        }
        ResumeThread(process.hThread);
        CloseHandle(process.hThread);
        session->process = process.hProcess;
        return SPX_OK;
    } catch (...) {
        return Fail(result, SPX_ERR_INTERNAL, L"启动游戏时出错");
    }
}

SpxError PostInstall(void*, SpxResult*) { return SPX_OK; }

// 游戏还在运行时返回 SPX_ERR_TIMEOUT，宿主会继续轮询。
SpxError WaitExit(void* handle, uint32_t timeout_ms, SpxResult* result) {
    auto* session = static_cast<Session*>(handle);
    if (session == nullptr || session->process == nullptr) {
        return Fail(result, SPX_ERR_PROCESS_NOT_FOUND, L"没有正在等待的游戏进程");
    }
    switch (WaitForSingleObject(session->process, timeout_ms)) {
        case WAIT_OBJECT_0: return SPX_OK;
        case WAIT_TIMEOUT: return SPX_ERR_TIMEOUT;
        default: return Fail(result, SPX_ERR_INTERNAL, L"等待游戏进程失败", 4, GetLastError());
    }
}

SpxError SetParam(void*, SpxStr, int64_t, SpxResult* result) {
    return Fail(result, SPX_ERR_NOT_SUPPORTED, L"示例插件不支持运行中调整");
}

// 进入 Running 之前的取消：结束我们刚创建的进程。
SpxError Abort(void* handle, SpxResult*) {
    auto* session = static_cast<Session*>(handle);
    if (session != nullptr && session->process != nullptr && WaitForSingleObject(session->process, 0) == WAIT_TIMEOUT) {
        TerminateProcess(session->process, 1);
    }
    return SPX_OK;
}

// 必须幂等。会话对象本身不释放，重复 release 不会碰到悬空指针（一个进程里只有很少几个会话）。
SpxError Release(void* handle) {
    auto* session = static_cast<Session*>(handle);
    if (session != nullptr && session->process != nullptr) {
        CloseHandle(session->process);
        session->process = nullptr;
    }
    return SPX_OK;
}

const SpxStr kExeNames[] = {Str(kExe)};

SpxError GetInfo(const SpxModuleInfo** out) {
    if (out == nullptr) {
        return SPX_ERR_INVALID_ARGUMENT;
    }
    static const SpxModuleInfo info = {
        sizeof(SpxModuleInfo), SPX_MODULE_ABI_MAJOR, SPX_MODULE_ABI_MINOR,
        Str(kId), Str(kName), Str(kVersion), kCaps, kExeNames, 1,
    };
    *out = &info;
    return SPX_OK;
}

const SpxModuleApi kApi = {
    sizeof(SpxModuleApi), SPX_MODULE_ABI_MAJOR, SPX_MODULE_ABI_MINOR,
    GetInfo, CreateSession, PrepareLaunch, AttachJob, Install, PostInstall, WaitExit, SetParam, Abort, Release,
};

}  // namespace

extern "C" __declspec(dllexport) SpxError __cdecl sparxie_module_query(
    uint32_t host_abi_major, uint32_t, const SpxModuleApi** out) {
    if (out == nullptr) {
        return SPX_ERR_INVALID_ARGUMENT;
    }
    if (host_abi_major != SPX_MODULE_ABI_MAJOR) {
        return SPX_ERR_ABI_MISMATCH;
    }
    *out = &kApi;
    return SPX_OK;
}

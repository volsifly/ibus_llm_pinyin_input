#include <fcitx-utils/key.h>
#include <fcitx-utils/log.h>
#include <fcitx/addonfactory.h>
#include <fcitx/addonmanager.h>
#include <fcitx/candidatelist.h>
#include <fcitx/inputcontext.h>
#include <fcitx/inputcontextmanager.h>
#include <fcitx/inputcontextproperty.h>
#include <fcitx/inputmethodengine.h>
#include <fcitx/inputpanel.h>
#include <fcitx/instance.h>
#include <fcitx/text.h>
#include <fcitx/userinterface.h>

#include <array>
#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <sstream>
#include <string>
#include <sys/wait.h>
#include <vector>

namespace {

constexpr const char *kStateName = "aiPinyinState";

std::string shellQuote(const std::string &value) {
    std::string result = "'";
    for (char ch : value) {
        if (ch == '\'') {
            result += "'\\''";
        } else {
            result += ch;
        }
    }
    result += "'";
    return result;
}

std::string repoDir() {
    const char *env = std::getenv("AI_PINYIN_REPO_DIR");
    if (env && *env) {
        return env;
    }
    return std::string(std::getenv("HOME")) + "/.local/share/ibus-ai-pinyin";
}

std::vector<std::string> splitLines(const std::string &text) {
    std::vector<std::string> result;
    std::istringstream stream(text);
    std::string line;
    while (std::getline(stream, line)) {
        if (!line.empty() && line.back() == '\r') {
            line.pop_back();
        }
        if (!line.empty()) {
            result.push_back(line);
        }
    }
    return result;
}

bool startsWith(const std::string &text, const std::string &prefix) {
    return text.rfind(prefix, 0) == 0;
}

std::string runCommand(const std::string &command) {
    std::array<char, 4096> buffer{};
    std::string output;
    FILE *pipe = popen(command.c_str(), "r");
    if (!pipe) {
        return output;
    }
    while (fgets(buffer.data(), buffer.size(), pipe)) {
        output += buffer.data();
    }
    int status = pclose(pipe);
    if (status == -1 || !WIFEXITED(status) || WEXITSTATUS(status) != 0) {
        return {};
    }
    return output;
}

struct AIPinyinState : public fcitx::InputContextProperty {
    std::string buffer;
    std::vector<std::string> candidates;
    int cursor = 0;
    int page = 0;
    int pageSize = 5;
    int requestId = 0;
    bool requesting = false;
    bool llmSource = false;
};

class AIPinyinEngine;

class AIPinyinCandidateWord : public fcitx::CandidateWord {
public:
    AIPinyinCandidateWord(AIPinyinEngine *engine, std::string text)
        : fcitx::CandidateWord(fcitx::Text(text)), engine_(engine),
          text_(std::move(text)) {}

    void select(fcitx::InputContext *inputContext) const override;

private:
    AIPinyinEngine *engine_;
    std::string text_;
};

class AIPinyinEngine : public fcitx::InputMethodEngineV2 {
public:
    explicit AIPinyinEngine(fcitx::Instance *instance)
        : instance_(instance),
          factory_([](fcitx::InputContext &) { return new AIPinyinState; }) {
        instance_->inputContextManager().registerProperty(kStateName, &factory_);
    }

    ~AIPinyinEngine() override {
        factory_.unregister();
    }

    void keyEvent(const fcitx::InputMethodEntry &, fcitx::KeyEvent &event) override {
        if (event.isRelease()) {
            return;
        }

        auto *ic = event.inputContext();
        auto *state = ic->propertyFor(&factory_);
        const fcitx::Key key = event.key();

        if (handleCandidateSelection(ic, state, key)) {
            event.filterAndAccept();
            return;
        }

        if (key.check(FcitxKey_space)) {
            if (!state->candidates.empty()) {
                commitCandidate(ic, state, state->cursor);
                event.filterAndAccept();
                return;
            }
            if (!state->buffer.empty()) {
                requestCandidates(ic, state);
                event.filterAndAccept();
                return;
            }
        }

        if (!state->candidates.empty() && isCandidatePageKey(key)) {
            movePage(ic, state, candidatePageDirection(key));
            event.filterAndAccept();
            return;
        }

        if (key.check(FcitxKey_Return)) {
            if (!state->candidates.empty()) {
                commitCandidate(ic, state, state->cursor);
                event.filterAndAccept();
                return;
            }
            if (!state->buffer.empty()) {
                ic->commitString(state->buffer);
                clear(ic, state);
                event.filterAndAccept();
                return;
            }
        }

        if (key.check(FcitxKey_Escape)) {
            if (!state->buffer.empty() || !state->candidates.empty()) {
                clear(ic, state);
                event.filterAndAccept();
            }
            return;
        }

        if (key.check(FcitxKey_BackSpace)) {
            if (!state->candidates.empty()) {
                state->candidates.clear();
                updateUI(ic, state);
                event.filterAndAccept();
                return;
            }
            if (!state->buffer.empty()) {
                state->buffer.pop_back();
                updateUI(ic, state);
                event.filterAndAccept();
            }
            return;
        }

        if (!state->candidates.empty() &&
            (key.check(FcitxKey_Up) || key.check(FcitxKey_Down))) {
            const int delta = key.check(FcitxKey_Up) ? -1 : 1;
            int pageStart = state->page * state->pageSize;
            int pageEnd = std::min(pageStart + state->pageSize,
                                   static_cast<int>(state->candidates.size()));
            int size = std::max(1, pageEnd - pageStart);
            int offset = state->cursor - pageStart;
            state->cursor = pageStart + ((offset + delta + size) % size);
            updateUI(ic, state);
            event.filterAndAccept();
            return;
        }

        if (shouldPassthroughKey(key, !state->candidates.empty())) {
            if (!state->buffer.empty() || !state->candidates.empty()) {
                clear(ic, state);
            }
            return;
        }

        if (key.hasModifier()) {
            if (!state->buffer.empty() || !state->candidates.empty()) {
                clear(ic, state);
            }
            return;
        }

        std::string text = fcitx::Key::keySymToUTF8(key.sym());
        if (state->buffer.empty() && state->candidates.empty() && text.size() == 1 &&
            shouldPassthroughInitialChar(text[0])) {
            return;
        }
        if (text.size() == 1 && acceptChar(text[0])) {
            if (!state->candidates.empty()) {
                state->candidates.clear();
            }
            state->buffer += static_cast<char>(std::tolower(text[0]));
            updateUI(ic, state);
            event.filterAndAccept();
        }
    }

    void reset(const fcitx::InputMethodEntry &, fcitx::InputContextEvent &event) override {
        auto *ic = event.inputContext();
        clear(ic, ic->propertyFor(&factory_));
    }

    void commitCandidate(fcitx::InputContext *ic, AIPinyinState *state, int index) {
        if (index < 0 || index >= static_cast<int>(state->candidates.size())) {
            return;
        }
        std::string pinyin = state->buffer;
        std::string text = state->candidates[index];
        ic->commitString(text);
        recordCommit(pinyin, text);
        clear(ic, state);
    }

    void commitCandidateText(fcitx::InputContext *ic, AIPinyinState *state,
                             const std::string &text) {
        for (size_t i = 0; i < state->candidates.size(); i++) {
            if (state->candidates[i] == text) {
                commitCandidate(ic, state, static_cast<int>(i));
                return;
            }
        }
        ic->commitString(text);
        clear(ic, state);
    }

private:
    bool acceptChar(char ch) const {
        return std::isalpha(static_cast<unsigned char>(ch)) || ch == '\'';
    }

    bool shouldPassthroughInitialChar(char ch) const {
        return std::isdigit(static_cast<unsigned char>(ch));
    }

    bool shouldPassthroughKey(const fcitx::Key &key, bool hasCandidates = false) const {
        auto sym = key.sym();
        if (hasCandidates &&
            (sym == FcitxKey_Up || sym == FcitxKey_Down ||
             sym == FcitxKey_Page_Up || sym == FcitxKey_Page_Down)) {
            return false;
        }
        return sym == FcitxKey_Tab || sym == FcitxKey_ISO_Left_Tab ||
               sym == FcitxKey_Left || sym == FcitxKey_Right ||
               sym == FcitxKey_Up || sym == FcitxKey_Down ||
               sym == FcitxKey_Home || sym == FcitxKey_End ||
               sym == FcitxKey_Page_Up || sym == FcitxKey_Page_Down ||
               sym == FcitxKey_Insert || sym == FcitxKey_Delete ||
               (sym >= FcitxKey_F1 && sym <= FcitxKey_F35);
    }

    bool isCandidatePageKey(const fcitx::Key &key) const {
        auto sym = key.sym();
        return sym == FcitxKey_equal || sym == FcitxKey_plus ||
               sym == FcitxKey_KP_Add || sym == FcitxKey_minus ||
               sym == FcitxKey_KP_Subtract || sym == FcitxKey_Page_Up ||
               sym == FcitxKey_Page_Down;
    }

    int candidatePageDirection(const fcitx::Key &key) const {
        auto sym = key.sym();
        if (sym == FcitxKey_minus || sym == FcitxKey_KP_Subtract ||
            sym == FcitxKey_Page_Up) {
            return -1;
        }
        return 1;
    }

    void movePage(fcitx::InputContext *ic, AIPinyinState *state, int direction) {
        int pageCount = (state->candidates.size() + state->pageSize - 1) /
                        state->pageSize;
        if (pageCount <= 1) {
            return;
        }
        state->page = std::max(0, std::min(pageCount - 1, state->page + direction));
        state->cursor = state->page * state->pageSize;
        updateUI(ic, state);
    }

    bool handleCandidateSelection(fcitx::InputContext *ic, AIPinyinState *state,
                                  const fcitx::Key &key) {
        if (state->candidates.empty()) {
            return false;
        }
        int index = key.digitSelection();
        int globalIndex = state->page * state->pageSize + index;
        if (index >= 0 && index < state->pageSize &&
            globalIndex < static_cast<int>(state->candidates.size())) {
            commitCandidate(ic, state, globalIndex);
            return true;
        }
        return false;
    }

    void requestCandidates(fcitx::InputContext *ic, AIPinyinState *state) {
        if (state->requesting) {
            return;
        }
        std::string pinyin = state->buffer;
        state->requesting = true;
        state->candidates.clear();
        state->cursor = 0;
        state->page = 0;
        updateUI(ic, state, " ...");

        std::string command = "timeout 3s python3 " +
                              shellQuote(repoDir() + "/fcitx5_backend.py") +
                              " candidates " + shellQuote(pinyin) +
                              " --metadata 2>/dev/null";
        auto candidates = splitLines(runCommand(command));
        state->llmSource = false;
        std::vector<std::string> filtered;
        filtered.reserve(candidates.size());
        for (const auto &candidate : candidates) {
            if (candidate == "__source__:llm") {
                state->llmSource = true;
                continue;
            }
            if (startsWith(candidate, "__source__:")) {
                continue;
            }
            filtered.push_back(candidate);
        }
        state->requesting = false;
        state->candidates = filtered.empty() ? std::vector<std::string>{pinyin}
                                             : std::move(filtered);
        state->cursor = 0;
        state->page = 0;
        updateUI(ic, state);
    }

    void recordCommit(const std::string &pinyin, const std::string &text) {
        std::string command = "python3 " + shellQuote(repoDir() + "/fcitx5_backend.py") +
                              " commit " + shellQuote(pinyin) + " " + shellQuote(text) +
                              " >/dev/null 2>&1";
        std::system(command.c_str());
    }

    void updateUI(fcitx::InputContext *ic, AIPinyinState *state,
                  const std::string &suffix = "") {
        auto &panel = ic->inputPanel();
        panel.setClientPreedit(fcitx::Text());
        panel.setPreedit(fcitx::Text());
        std::string aux = state->buffer + suffix;
        if (state->requesting) {
            aux += "  [LLM请求中]";
        } else if (state->llmSource && !state->candidates.empty()) {
            aux += "  [LLM]";
        }
        panel.setAuxUp(fcitx::Text(aux));
        panel.setAuxDown(fcitx::Text());

        if (!state->candidates.empty()) {
            auto list = std::make_unique<fcitx::DisplayOnlyCandidateList>();
            std::vector<std::string> labels;
            int pageStart = state->page * state->pageSize;
            int pageEnd = std::min(pageStart + state->pageSize,
                                   static_cast<int>(state->candidates.size()));
            labels.reserve(pageEnd - pageStart);
            for (int i = pageStart; i < pageEnd; i++) {
                labels.push_back(std::to_string(i - pageStart + 1) + ". " +
                                 state->candidates[i]);
            }
            list->setContent(labels);
            list->setCursorIndex(state->cursor - pageStart);
            panel.setCandidateList(std::move(list));
        } else {
            panel.setCandidateList(nullptr);
        }
        ic->updatePreedit();
        ic->updateUserInterface(fcitx::UserInterfaceComponent::InputPanel, true);
    }

    void clear(fcitx::InputContext *ic, AIPinyinState *state) {
        state->buffer.clear();
        state->candidates.clear();
        state->cursor = 0;
        state->page = 0;
        state->requestId++;
        state->requesting = false;
        state->llmSource = false;
        updateUI(ic, state);
    }

    fcitx::Instance *instance_;
    fcitx::FactoryFor<AIPinyinState> factory_;
};

void AIPinyinCandidateWord::select(fcitx::InputContext *inputContext) const {
    auto *state = static_cast<AIPinyinState *>(inputContext->property(kStateName));
    if (!state) {
        return;
    }
    engine_->commitCandidateText(inputContext, state, text_);
}

class AIPinyinEngineFactory : public fcitx::AddonFactory {
public:
    fcitx::AddonInstance *create(fcitx::AddonManager *manager) override {
        return new AIPinyinEngine(manager->instance());
    }
};

} // namespace

FCITX_ADDON_FACTORY(AIPinyinEngineFactory);

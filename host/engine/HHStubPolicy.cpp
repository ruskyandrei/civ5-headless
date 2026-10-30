// Engine methods the host only stubs (gen/HHStubs.gen.h) that are known to be harmless when the DLL calls them: the
// stub's answer (nothing done; false, 0 or NULL returned) is what an autoplay game needs, shown by the parity work
// against the real game (docs/history) or by what the method is for. The run report (host-report.json) lists every
// stub a run called and whether it is on this list; a stub that is not is how a DLL that needs new engine support
// shows up.
#include "HHCommon.h"
#include "engine/HHRuntime.h"

namespace
{
	struct Rule
	{
		const char* szName;   // exact method, or a prefix ending in '*'
		const char* szWhy;
	};

	const Rule kRules[] =
	{
		{ "ICvEngineUtility1::Gameplay*", "visual notification to the renderer (unit and city graphics, effects, fog, minimap)" },
		{ "ICvEngineUtility3::TradeVisuals_*", "trade route graphics" },
		{ "ICvEngineUtility1::PublishActivePlayer", "UI event (the UI's GameplaySetActivePlayer)" },
		{ "ICvEngineUtility1::PublishEraChanges", "UI event" },
		{ "ICvEngineUtility1::DoTurn", "the engine's per-turn UI refresh" },
		{ "ICvEngineUtility1::DoClearDiplomacyTradeTable", "diplomacy screen" },
		{ "ICvEngineUtility1::NotifyAILeadersInGame", "leader scene assets" },
		{ "ICvEngineUtility1::NotifySpecificAILeaderInGame", "leader scene assets" },
		{ "ICvEngineUtility3::FlushTurnReminders", "UI turn reminders" },
		{ "ICvEngineUtility1::getAssignedNetworkID", "CvPlayer::Read of a loaded game: the DLL only stores the player's net ID in the pregame, for network games" },
		{ "ICvEngineUtility1::stripSpecialCharacters", "formats a display string in place; names stay as given (M5 parity)" },
		{ "ICvEngineUtility1::GetAdvisorBadAttackInterrupt", "advisor interrupt user setting, off (M5 parity)" },
		{ "ICvEngineUtility1::GetAdvisorCityAttackInterrupt", "advisor interrupt user setting, off (M5 parity)" },
		{ "ICvEngineUtility1::SetAdvisorBadAttackInterrupt", "advisor setting" },
		{ "ICvEngineUtility1::SetAdvisorCityAttackInterrupt", "advisor setting" },
		{ "ICvEngineUtility1::UnlockAchievement", "Steam achievement" },
		{ "ICvEngineUtility1::IsAchievementUnlocked", "Steam achievement (none unlocked)" },
		{ "ICvEngineUtility1::IncrementSteamStat*", "Steam statistic" },
		{ "ICvEngineUtility1::GetSteamStat", "Steam statistic (none)" },
		{ "ICvEngineUtility1::SetSteamStat", "Steam statistic" },
		{ "ICvEngineUtility1::RecordLeaderboardScore", "Steam leaderboard" },
		{ "ICvEngineUtility1::RecordVictoryInformation", "Steam statistic at the end of a game" },
		{ "ICvEngineUtility1::saveReplay", "replay file of a finished game" },
		{ "ICvUserInterface1::Add*Message", "message log of the active player's screen" },
		{ "ICvUserInterface1::AddMessage", "message log of the active player's screen" },
		{ "ICvUserInterface1::AddPopupText", "popup text of the active player's screen" },
		{ "ICvUserInterface1::AddDeferredWonderCommand", "wonder graphics" },
		{ "ICvUserInterface1::AddHexToUIRange", "range highlight" },
		{ "ICvUserInterface1::IsPolicyNotificationSeen", "UI flag; the notification shows (M5 parity)" },
		{ "ICvUserInterface1::SetPolicyNotificationSeen", "UI flag" },
		{ "ICvUserInterface1::IsSPAutoEndTurnEnabled", "user setting, off (M5 parity)" },
		{ "ICvUserInterface1::RemoveFromSelectionList", "unit selection of the screen" },
		{ "ICvUserInterface1::SetSpecificCityInfoDirty", "city banner refresh" },
		{ "ICvUserInterface1::UpdateCountryBorder", "border graphics" },
		{ "ICvUserInterface1::UpdateAllCountryBorders", "border graphics" },
		{ "ICvUserInterface1::changeCycleSelectionCounter", "unit cycle of the screen" },
		{ "ICvUserInterface1::doTurn", "the screen's per-turn refresh" },
		{ "ICvUserInterface1::lookAt*", "camera" },
		{ "ICvUserInterface1::centerCamera", "camera" },
		{ "ICvUserInterface1::releaseLockedCamera", "camera" },
		{ "ICvUserInterface1::setCanEndTurn", "end-turn button" },
		{ "ICvUserInterface1::setHasMovedUnit", "end-turn button" },
		{ "ICvUserInterface1::setNoSelectionListCycle", "unit cycle of the screen" },
		{ "ICvUserInterface1::setInterfaceMode", "interface mode of the screen" },
		{ "ICvUserInterface1::setFlashing", "flashing player icon" },
		{ "ICvUserInterface1::setForcePopup", "popup flag" },
		{ "ICvUserInterface1::setLastSelectedUnit", "UI selection" },
		{ "ICvUserInterface1::selectUnit", "unit selection of the screen" },
		{ "ICvUserInterface1::selectGroup", "unit selection of the screen" },
		{ "ICvUserInterface1::selectCity", "city selection of the screen" },
		{ "ICvUserInterface1::selectLookAtCity", "city selection of the screen" },
		{ "ICvUserInterface1::addSelectedCity", "city selection of the screen" },
		{ "ICvUserInterface1::VisuallyDeselectUnit", "unit graphics" },
		{ "ICvUserInterface1::refreshYieldVisibleMode", "map overlay" },
		{ "ICvUserInterface1::toggle*", "map overlay" },
		{ "ICvUserInterface1::updateCityScreen", "city screen" },
		{ "ICvUserInterface1::updateCursorType", "cursor" },
		{ "ICvUserInterface1::updateEndTurnTimer", "turn timer display" },
		{ "ICvUserInterface1::clearEventMessages", "message log" },
		{ "ICvUserInterface1::DebugCityRoutePlots", "debug display" },
		{ "ICvUserInterface1::exitingToMainMenu", "front end" },
		{ "ICvUserInterface1::OpenTechTree", "tech tree screen" },
		{ "ICvUserInterface1::OpenEndGameMenu", "end-of-game screen (the stock DLL ends the game when autoplay ends with the human player dead)" },
		{ "ICvUserInterface1::PublishRemotePlayerTurnEnd", "UI event" },
		{ "ICvUserInterface2::PublishRemotePlayerTurnStart", "UI event" },
	};
}

bool HHIsHarmlessStub(const std::string& strMethod, std::string* pWhy)
{
	for(size_t i = 0; i < sizeof(kRules) / sizeof(kRules[0]); ++i)
	{
		const std::string rule = kRules[i].szName;
		const size_t star = rule.find('*');
		bool bMatch = false;
		if(star == std::string::npos)
			bMatch = strMethod == rule;
		else if(strMethod.compare(0, star, rule, 0, star) == 0)
		{
			// One '*' at the end (prefix) or in the middle (prefix ... suffix).
			const std::string suffix = rule.substr(star + 1);
			const size_t mid = suffix.find('*');
			if(suffix.empty())
				bMatch = true;
			else if(mid != std::string::npos)
				bMatch = strMethod.find(suffix.substr(0, mid), star) != std::string::npos;
			else
				bMatch = strMethod.size() >= star + suffix.size() && strMethod.compare(strMethod.size() - suffix.size(), suffix.size(), suffix) == 0;
		}
		if(bMatch)
		{
			if(pWhy)
				*pWhy = kRules[i].szWhy;
			return true;
		}
	}
	return false;
}

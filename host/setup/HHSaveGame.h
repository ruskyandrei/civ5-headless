// Saved games: the game's own .Civ5Save files, written at the engine's autosave points and on request, and read
// back to continue a game the real game or the host saved (docs/saves.md).
//
// A .Civ5Save is the engine's container around what the DLL serializes (every save of game build 403694; strings
// are a u32 byte count and the bytes, numbers little-endian):
//   "CIV5", u32 8, game version "1.0.3.279 (403694)", build "403694"
//   u32 game turn, u8 game type, then type names: the active player's civilization and handicap, the start era,
//     the active player's current era, game speed, world size; the map script
//   u32 n, n x (DLC GUID, u32 1, name);  u32 n, n x (mod ID, u32 version, name), activation order
//   the active player's leader name, civilization name and player colour
//   the DLL's GUID and version, GUID {0D41152C-948E-498F-8CDF-699D04C98A95}, u32 0
//   ICvPreGame1::write
//   u32 2, then one zlib stream (zlib1.dll of the game, default level, one sync flush at the end) cut into chunks of
//     at most 64 KiB, each a u32 byte count and the bytes. Inflated: ICvGame1::Write (it embeds the save database,
//     <cache>\Civ5SavedGameDatabase.db), ICvMap1::Write, ICvTeam1::Write x 64, ICvPlayer1::Write x 64,
//     ICvGame1::WriteSupportingClassData.
#pragma once
#include "HHCommon.h"

struct HHNewGameInterfaces;
struct HHNewGameResult;

// The stream the DLL's serializers write to and read from: a byte buffer in memory. A read past the end gives zeros
// and marks the stream as overrun.
class HHMemoryStream : public FDataStream
{
public:
	HHMemoryStream();
	HHMemoryStream(const char* pData, size_t uiSize);
	virtual ~HHMemoryStream();
	virtual unsigned int ReadIt(unsigned int byteCount, void* buffer);
	virtual unsigned int WriteIt(unsigned int byteCount, const void* buffer);
	virtual void Skip(unsigned int deltaByteCount);
	virtual void Rewind();
	virtual bool AtEnd() const;
	virtual void FastFwd();
	virtual unsigned int GetBytesUsed() const;
	virtual unsigned int GetPosition() const;
	virtual void SetPosition(unsigned int position);
	virtual void Truncate();
	virtual void Flush();
	virtual unsigned int GetEOF() const;
	virtual unsigned int GetSizeLeft() const;
	virtual void CopyToMem(void* mem);
	virtual void RaiseException(std::exception& kException) const;

	const std::vector<char>& Data() const { return m_data; }
	bool Overrun() const { return m_bOverrun; }

private:
	std::vector<char> m_data;
	size_t m_uiPos;
	bool m_bOverrun;
};

struct HHSaveDlc
{
	GUID id;
	std::string name;
};

struct HHSaveMod
{
	std::string id;
	int version;
	std::string name;
};

// What a save's header says (the part before ICvPreGame1::write).
struct HHSaveHeader
{
	unsigned int saveVersion;
	std::string gameVersion, gameBuild;
	int turn, gameType;
	std::string civilization, handicap, startEra, currentEra, gameSpeed, worldSize, mapScript;
	std::vector<HHSaveDlc> dlc;
	std::vector<HHSaveMod> mods;
	std::string leaderName, civilizationName, playerColor;
	GUID dllGuid;
	std::string dllVersion;
	size_t pregameOffset;   // where ICvPreGame1::write starts in the file
	HHSaveHeader() : saveVersion(0), turn(0), gameType(0), pregameOffset(0) { memset(&dllGuid, 0, sizeof(dllGuid)); }
};

// A save the run wrote (host-report.json "saves").
struct HHSaveRecord
{
	std::string kind;       // autosave, turn, final
	std::string path;
	int turn;
	unsigned int bytes, bodyBytes;
	double seconds;
	std::string error;
};

// What the run loaded (host-report.json "load").
struct HHLoadRecord
{
	std::string path;
	HHSaveHeader header;
	unsigned int bodyBytes;
	double seconds;
	std::vector<std::string> warnings;
	bool bReseeded;                     // --reseed: seeds after the load (-1: the save's stayed)
	__int64 reseedMap, reseedSync;
	unsigned __int64 rngBefore, rngAfter; // the game generator's seed around the reseed (with a new sync seed)
	HHLoadRecord() : bodyBytes(0), seconds(0.0), bReseeded(false), reseedMap(-1), reseedSync(-1), rngBefore(0), rngAfter(0) {}
};

// Reads a save's header; false (strError) when the file is not a .Civ5Save this host reads.
bool HHReadSaveHeader(const std::string& strPath, HHSaveHeader& kHeader, std::string& strError);
std::string HHGuidString(const GUID& g);

// Loads a .Civ5Save into the booted DLL and leaves the game as the engine leaves a loaded game before its load
// screen closes (for loadgame: instead of HHApplyPregame and HHStartNewGame). kResult gets the steps and timings.
bool HHLoadSavedGame(const std::string& strPath, HHNewGameInterfaces& kIf, HHNewGameResult& kResult);
// True once a saved game has been loaded (ICvUserInterface1::IsLoadedGame, UI.IsLoadedGame).
bool HHIsLoadedGame();
const HHLoadRecord* HHGetLoadRecord();

// The game the saves are taken from: set once the game has started or loaded (NULL: none, saves are skipped).
void HHSetSaveGame(HHNewGameInterfaces* pIf);
// ICvEngineUtility1::AutoSave, the engine's autosave point (bInitial: the first update of a new game; otherwise once
// per turn): writes the --autosave and --save-at saves that fall on it.
void HHOnAutoSave(bool bInitial, bool bPostTurn);
// --save-final: the game as it is once autoplay has ended.
void HHSaveFinal();
// UI.SaveGame(name), the in-game Save menu's call: <save dir>\<name>.Civ5Save now (kind "lua"); false without a game.
bool HHSaveNamed(const std::string& strName);
const std::vector<HHSaveRecord>& HHGetSaveRecords();

// Virtual file system for Lua (H10): the files `include` and `LoadFile` can reach, as the
// engine builds them for a game with mods.
//
// Sources, from lowest to highest precedence:
//   tier 0  base game: every .lua below <game>\Assets except Assets\DLC;
//   tier 1  DLC packages: the <Directory>/<MapDirectory> folders listed in each
//           Assets\DLC\<folder>\*.Civ5Pkg, ordered by the package <Priority> (default 0);
//   tier 2  activated mods: ModFiles rows with Import=1, in activation order (Mods.RowID);
//           a later-activated mod wins.
// A name is resolved by its file name (case-insensitive, ".lua" optional); the entry with the
// highest (tier, rank) wins. Measured in the real game (probe hh-probe-include-20261025a):
// VP's AssignStartingPlots beats CP's, CP's MapmakerUtilities beats DLC and base.
// Inside one DLC package the file scanned last wins (its <Directory> list in order, each folder's files
// before its subfolders, names sorted): a Vox Populi DLC modpack ships CP's and VP's copies of a file in
// Mods\(1) Community Patch and Mods\(2) Vox Populi, and the real game plays VP's (Communitu_79a needs VP's
// AssignStartingPlots). No stock package has two Lua files with one name.
#pragma once
#include "HHCommon.h"

struct HHVfsEntry
{
	std::string path;    // absolute file path
	std::string source;  // "base", "dlc:<folder>/<package>", "mod:<name>"
	int tier;            // 0 base, 1 DLC, 2 mod
	int rank;            // DLC priority or mod activation index
	int group;           // which source scan added it (packages of one priority: first scanned wins)
	int seq;             // insertion order (ties inside a group: first wins, inside a DLC package last)
};

struct HHModEntryPoint
{
	std::string type, name, description, file;
};

struct HHActivatedMod
{
	std::string id;
	int version;
	std::string name;
	std::string folder;   // re-rooted mod folder
	std::vector<std::pair<std::string, std::string> > properties;
	std::vector<HHModEntryPoint> entryPoints;
};

class HHVfs
{
public:
	HHVfs();

	// gameDir: game install folder. modsDb: the run's copy of Civ5ModsDatabase.db (may be empty:
	// no mods). modsDir: folder the mods' EvaluatedPath values are re-rooted to (empty: as stored).
	bool Build(const std::string& strGameDir, const std::string& strModsDbCopy, const std::string& strModsDir);

	// Best entry first; NULL if nothing has that name.
	const std::vector<HHVfsEntry>* Find(const std::string& strName) const;

	// A path recorded by the engine under the original MODS folder, re-rooted to --mods-dir.
	std::string Reroot(const std::string& strPath) const;

	const std::vector<HHActivatedMod>& GetMods() const { return m_mods; }
	std::vector<std::string> AllPaths() const; // every indexed file, best entry per name, sorted by key

	// Resolution log (<run>\Logs\include-resolution.log): one block per distinct request.
	void LogResolution(const char* szKind, const std::string& strRequest, const std::string& strContext,
	                   const std::string& strChosen, const std::vector<HHVfsEntry>* pCandidates);
	void WriteIndexLog(const std::string& strPath) const;

	static std::string Key(const std::string& strName);

private:
	void Add(const std::string& strPath, const std::string& strSource, int iTier, int iRank);
	void NextGroup() { ++m_iGroup; }
	void ScanTree(const std::wstring& wstrDir, const std::string& strSource, int iTier, int iRank, bool bSkipDlc, int iDepth);
	void ScanDlc(const std::string& strGameDir);
	bool ReadMods(const std::string& strModsDbCopy, const std::string& strModsDir);
	void Sort();

	std::map<std::string, std::vector<HHVfsEntry> > m_index;
	std::vector<HHActivatedMod> m_mods;
	std::vector<std::string> m_sourcesLog;
	std::string m_strModsDir;
	std::set<std::string> m_logged;
	int m_iSeq;
	int m_iGroup;
};

HHVfs& HHGetVfs();

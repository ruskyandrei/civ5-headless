#include "setup/HHVfs.h"
#include "engine/HHRuntime.h"
#include "engine/HHLogFiles.h"

namespace
{
	bool EndsWithNoCase(const std::string& s, const char* szSuffix)
	{
		const size_t n = strlen(szSuffix);
		return s.size() >= n && _stricmp(s.c_str() + s.size() - n, szSuffix) == 0;
	}

	std::string BackSlashes(std::string s)
	{
		for(size_t i = 0; i < s.size(); ++i)
			if(s[i] == '/')
				s[i] = '\\';
		return s;
	}

	std::string ReadText(const std::wstring& wstrPath)
	{
		std::string text;
		FILE* f = _wfopen(wstrPath.c_str(), L"rb");
		if(!f)
			return text;
		char buf[16384];
		size_t n;
		while((n = fread(buf, 1, sizeof(buf), f)) > 0)
			text.append(buf, n);
		fclose(f);
		return text;
	}

	// Values of every <szTag>...</szTag> in the text (a .Civ5Pkg is flat enough for this).
	std::vector<std::string> TagValues(const std::string& text, const char* szTag)
	{
		std::vector<std::string> out;
		const std::string open = std::string("<") + szTag + ">";
		const std::string close = std::string("</") + szTag + ">";
		size_t pos = 0;
		while((pos = text.find(open, pos)) != std::string::npos)
		{
			pos += open.size();
			size_t end = text.find(close, pos);
			if(end == std::string::npos)
				break;
			out.push_back(text.substr(pos, end - pos));
			pos = end + close.size();
		}
		return out;
	}

	bool IsDirectory(const std::wstring& w)
	{
		DWORD attr = GetFileAttributesW(w.c_str());
		return attr != INVALID_FILE_ATTRIBUTES && (attr & FILE_ATTRIBUTE_DIRECTORY);
	}

	struct ByPriority
	{
		bool operator()(const HHVfsEntry& a, const HHVfsEntry& b) const
		{
			if(a.tier != b.tier) return a.tier > b.tier;
			if(a.rank != b.rank) return a.rank > b.rank;
			if(a.group != b.group) return a.group < b.group;
			return a.tier == 1 ? a.seq > b.seq : a.seq < b.seq;
		}
	};
}

HHVfs& HHGetVfs()
{
	static HHVfs s_vfs;
	return s_vfs;
}

HHVfs::HHVfs() : m_iSeq(0), m_iGroup(0) {}

std::string HHVfs::Key(const std::string& strName)
{
	std::string s = BackSlashes(strName);
	size_t slash = s.find_last_of('\\');
	if(slash != std::string::npos)
		s = s.substr(slash + 1);
	s = HHLower(s);
	if(EndsWithNoCase(s, ".lua"))
		s.erase(s.size() - 4);
	return s;
}

void HHVfs::Add(const std::string& strPath, const std::string& strSource, int iTier, int iRank)
{
	HHVfsEntry e;
	e.path = strPath;
	e.source = strSource;
	e.tier = iTier;
	e.rank = iRank;
	e.group = m_iGroup;
	e.seq = m_iSeq++;
	m_index[Key(strPath)].push_back(e);
}

void HHVfs::ScanTree(const std::wstring& wstrDir, const std::string& strSource, int iTier, int iRank, bool bSkipDlc, int iDepth)
{
	if(iDepth > 16)
		return;
	WIN32_FIND_DATAW fd;
	HANDLE h = FindFirstFileW((wstrDir + L"\\*").c_str(), &fd);
	if(h == INVALID_HANDLE_VALUE)
		return;
	std::vector<std::wstring> files, dirs;
	do
	{
		if(!wcscmp(fd.cFileName, L".") || !wcscmp(fd.cFileName, L".."))
			continue;
		if(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)
		{
			if(!(bSkipDlc && iDepth == 0 && !_wcsicmp(fd.cFileName, L"DLC")))
				dirs.push_back(fd.cFileName);
		}
		else
			files.push_back(fd.cFileName);
	} while(FindNextFileW(h, &fd));
	FindClose(h);
	// Deterministic order: files of a folder (sorted), then its subfolders (sorted).
	std::sort(files.begin(), files.end());
	std::sort(dirs.begin(), dirs.end());
	for(size_t i = 0; i < files.size(); ++i)
	{
		const std::string name = HHNarrow(files[i]);
		if(EndsWithNoCase(name, ".lua"))
			Add(HHNarrow(wstrDir + L"\\" + files[i]), strSource, iTier, iRank);
	}
	for(size_t i = 0; i < dirs.size(); ++i)
		ScanTree(wstrDir + L"\\" + dirs[i], strSource, iTier, iRank, bSkipDlc, iDepth + 1);
}

// ASSUMPTION (trace): every package below Assets\DLC is active (single player, all DLC owned).
// Packages with UISkin variants (UI_bc1, VPUI) are only active for the Expansion2 skin set.
// Packages without <Directory>/<MapDirectory> contribute no Lua to the VFS (their Lua is
// scenario content, loaded by the scenario's own entry points).
void HHVfs::ScanDlc(const std::string& strGameDir)
{
	const std::wstring dlcRoot = HHWiden(strGameDir + "\\Assets\\DLC");
	WIN32_FIND_DATAW fd;
	HANDLE h = FindFirstFileW((dlcRoot + L"\\*").c_str(), &fd);
	if(h == INVALID_HANDLE_VALUE)
		return;
	std::vector<std::wstring> folders;
	do
	{
		if((fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) && wcscmp(fd.cFileName, L".") && wcscmp(fd.cFileName, L".."))
			folders.push_back(fd.cFileName);
	} while(FindNextFileW(h, &fd));
	FindClose(h);
	std::sort(folders.begin(), folders.end());
	for(size_t i = 0; i < folders.size(); ++i)
	{
		const std::wstring folder = dlcRoot + L"\\" + folders[i];
		std::vector<std::wstring> pkgs;
		HANDLE hp = FindFirstFileW((folder + L"\\*.Civ5Pkg").c_str(), &fd);
		if(hp != INVALID_HANDLE_VALUE)
		{
			do pkgs.push_back(fd.cFileName); while(FindNextFileW(hp, &fd));
			FindClose(hp);
		}
		std::sort(pkgs.begin(), pkgs.end());
		for(size_t j = 0; j < pkgs.size(); ++j)
		{
			const std::string text = ReadText(folder + L"\\" + pkgs[j]);
			const std::string label = "dlc:" + HHNarrow(folders[i]) + "/" + HHNarrow(pkgs[j]);
			if(text.find("<UISkin") != std::string::npos && text.find("set=\"Expansion2\"") == std::string::npos)
			{
				m_sourcesLog.push_back(label + ": skipped (UISkin variant for another ruleset)");
				continue;
			}
			std::vector<std::string> prio = TagValues(text, "Priority");
			const int iPriority = prio.empty() ? 0 : atoi(prio[0].c_str());
			std::vector<std::string> dirs = TagValues(text, "Directory");
			std::vector<std::string> mapDirs = TagValues(text, "MapDirectory");
			dirs.insert(dirs.end(), mapDirs.begin(), mapDirs.end());
			const size_t before = m_iSeq;
			NextGroup();
			for(size_t k = 0; k < dirs.size(); ++k)
			{
				const std::wstring dir = folder + L"\\" + HHWiden(BackSlashes(dirs[k]));
				if(IsDirectory(dir))
					ScanTree(dir, label, 1, iPriority, false, 1);
			}
			m_sourcesLog.push_back(HHFormat("%s: priority %d, %u directories, %u Lua files", label.c_str(), iPriority,
			                                (unsigned)dirs.size(), (unsigned)(m_iSeq - before)));
		}
	}
}

std::string HHVfs::Reroot(const std::string& strPath) const
{
	std::string p = BackSlashes(strPath);
	if(m_strModsDir.empty())
		return p;
	const std::string lower = HHLower(p);
	const size_t pos = lower.find("\\mods\\");
	if(pos == std::string::npos)
		return p;
	return m_strModsDir + p.substr(pos + 5);
}

bool HHVfs::ReadMods(const std::string& strModsDbCopy, const std::string& strModsDir)
{
	Database::Connection db;
	if(!db.Open(strModsDbCopy.c_str(), Database::OPEN_READONLY))
	{
		HHLogError("cannot open the mods database copy %s", strModsDbCopy.c_str());
		return false;
	}
	// Activation order: Mods.RowID (the order the mod browser activated them).
	// ASSUMPTION (trace): matches the engine's activation order (CP, VP, EUI compat, Squads,
	// Small Resource Icons in the deployed setup).
	{
		Database::Results r;
		if(!db.Execute(r, "SELECT ModID, Version FROM Mods WHERE Activated = 1 ORDER BY RowID"))
			return false;
		while(r.Step())
		{
			HHActivatedMod m;
			m.id = r.GetText(0) ? r.GetText(0) : "";
			m.version = r.GetInt(1);
			m_mods.push_back(m);
		}
	}
	for(size_t i = 0; i < m_mods.size(); ++i)
	{
		HHActivatedMod& m = m_mods[i];
		{
			Database::Results r;
			db.Execute(r, "SELECT Name, Value FROM ModProperties WHERE ModID = ? AND Version = ? ORDER BY rowid");
			r.Bind(1, m.id.c_str());
			r.Bind(2, m.version);
			while(r.Step())
			{
				const std::string name = r.GetText(0) ? r.GetText(0) : "";
				const std::string value = r.GetText(1) ? r.GetText(1) : "";
				m.properties.push_back(std::make_pair(name, value));
				if(name == "Name")
					m.name = value;
			}
		}
		{
			Database::Results r;
			db.Execute(r, "SELECT Type, Name, Description, File FROM ModEntryPoints WHERE ModID = ? AND Version = ? ORDER BY rowid");
			r.Bind(1, m.id.c_str());
			r.Bind(2, m.version);
			while(r.Step())
			{
				HHModEntryPoint e;
				e.type = r.GetText(0) ? r.GetText(0) : "";
				e.name = r.GetText(1) ? r.GetText(1) : "";
				e.description = r.GetText(2) ? r.GetText(2) : "";
				e.file = r.GetText(3) ? r.GetText(3) : "";
				m.entryPoints.push_back(e);
			}
		}
		{
			Database::Results r;
			db.Execute(r, "SELECT Path, EvaluatedPath FROM ModFiles WHERE ModID = ? AND Version = ? AND Import = 1 ORDER BY rowid");
			r.Bind(1, m.id.c_str());
			r.Bind(2, m.version);
			const std::string label = "mod:" + (m.name.empty() ? m.id : m.name);
			int nLua = 0, nMissing = 0;
			while(r.Step())
			{
				const std::string rel = r.GetText(0) ? r.GetText(0) : "";
				const std::string evaluated = r.GetText(1) ? r.GetText(1) : "";
				if(!EndsWithNoCase(rel, ".lua"))
					continue;
				const std::string path = Reroot(evaluated);
				if(m.folder.empty())
				{
					const std::string relB = BackSlashes(rel);
					if(path.size() > relB.size() && _stricmp(path.c_str() + path.size() - relB.size(), relB.c_str()) == 0)
						m.folder = path.substr(0, path.size() - relB.size() - 1);
				}
				if(!HHFileExists(path))
				{
					++nMissing;
					HHLog("vfs: %s imports %s, which does not exist", label.c_str(), path.c_str());
					continue;
				}
				Add(path, label, 2, (int)i);
				++nLua;
			}
			m_sourcesLog.push_back(HHFormat("%s (%s v%d): activation %u, %d imported Lua files%s", label.c_str(), m.id.c_str(),
			                                m.version, (unsigned)i, nLua, nMissing ? HHFormat(", %d missing", nMissing).c_str() : ""));
		}
	}
	db.Close();
	UNUSED_VARIABLE(strModsDir);
	return true;
}

void HHVfs::Sort()
{
	for(std::map<std::string, std::vector<HHVfsEntry> >::iterator it = m_index.begin(); it != m_index.end(); ++it)
		std::sort(it->second.begin(), it->second.end(), ByPriority()); // total order (seq breaks ties); stable_sort would pull msvcprt nothrow.obj, which also defines _Init_locks::operator=
}

bool HHVfs::Build(const std::string& strGameDir, const std::string& strModsDbCopy, const std::string& strModsDir)
{
	m_index.clear();
	m_mods.clear();
	m_sourcesLog.clear();
	m_iSeq = 0;
	m_strModsDir = strModsDir;
	while(!m_strModsDir.empty() && (m_strModsDir[m_strModsDir.size() - 1] == '\\' || m_strModsDir[m_strModsDir.size() - 1] == '/'))
		m_strModsDir.erase(m_strModsDir.size() - 1);

	const int before = m_iSeq;
	ScanTree(HHWiden(strGameDir + "\\Assets"), "base", 0, 0, true, 0);
	m_sourcesLog.push_back(HHFormat("base: %d Lua files below Assets (Assets\\DLC excluded)", m_iSeq - before));
	ScanDlc(strGameDir);
	bool bOk = true;
	if(!strModsDbCopy.empty())
		bOk = ReadMods(strModsDbCopy, strModsDir);
	Sort();
	for(size_t i = 0; i < m_sourcesLog.size(); ++i)
		HHLog("vfs: %s", m_sourcesLog[i].c_str());
	HHLog("vfs: %u names, %d files", (unsigned)m_index.size(), m_iSeq);
	return bOk;
}

const std::vector<HHVfsEntry>* HHVfs::Find(const std::string& strName) const
{
	std::map<std::string, std::vector<HHVfsEntry> >::const_iterator it = m_index.find(Key(strName));
	return it == m_index.end() ? NULL : &it->second;
}

std::vector<std::string> HHVfs::AllPaths() const
{
	std::vector<std::string> out;
	for(std::map<std::string, std::vector<HHVfsEntry> >::const_iterator it = m_index.begin(); it != m_index.end(); ++it)
		out.push_back(it->second.front().path);
	return out;
}

void HHVfs::LogResolution(const char* szKind, const std::string& strRequest, const std::string& strContext,
                          const std::string& strChosen, const std::vector<HHVfsEntry>* pCandidates)
{
	const std::string key = std::string(szKind) + "|" + strRequest + "|" + strContext;
	if(m_logged.count(key))
		return;
	m_logged.insert(key);
	FILogFile* pLog = HHLogFileMgr::Get().GetLog("include-resolution.log", 0);
	pLog->Msg("%s(\"%s\") in %s -> %s", szKind, strRequest.c_str(), strContext.c_str(), strChosen.empty() ? "(not found)" : strChosen.c_str());
	if(pCandidates)
		for(size_t i = 0; i < pCandidates->size(); ++i)
		{
			const HHVfsEntry& e = (*pCandidates)[i];
			pLog->Msg("    %s [%s, tier %d, rank %d] %s", i == 0 ? "chosen" : "lost  ", e.source.c_str(), e.tier, e.rank, e.path.c_str());
		}
}

void HHVfs::WriteIndexLog(const std::string& strPath) const
{
	FILE* f = _wfopen(HHWiden(strPath).c_str(), L"w");
	if(!f)
		return;
	fprintf(f, "# VFS sources (lowest precedence first)\n");
	for(size_t i = 0; i < m_sourcesLog.size(); ++i)
		fprintf(f, "%s\n", m_sourcesLog[i].c_str());
	fprintf(f, "\n# Names with more than one file (winner first)\n");
	for(std::map<std::string, std::vector<HHVfsEntry> >::const_iterator it = m_index.begin(); it != m_index.end(); ++it)
	{
		if(it->second.size() < 2)
			continue;
		fprintf(f, "%s\n", it->first.c_str());
		for(size_t i = 0; i < it->second.size(); ++i)
			fprintf(f, "    [%s] %s\n", it->second[i].source.c_str(), it->second[i].path.c_str());
	}
	fclose(f);
}

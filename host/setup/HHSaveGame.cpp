// Saved games (setup/HHSaveGame.h; docs/saves.md).
#include "setup/HHSaveGame.h"
#include "setup/HHNewGame.h"
#include "setup/HHVfs.h"
#include "setup/HHJson.h"
#include "engine/HHRuntime.h"
#include "engine/HHEngineServices.h"
#include "lua/HHScriptSystem.h"

#define HH_STEP(text, call) do { call; kResult.steps.push_back(text); } while(0)

//------------------------------------------------------------------------------
// FDataStream: the FireWorks base class the DLL's serializers call through. Its non-inline members live in
// FireWorksWin32.lib, which the game DLL links and the host does not; these are the host's. The DLL's own
// operators swap the bytes of every value whose stream says a different endianness from the machine's.
//------------------------------------------------------------------------------
FDataStream::FDataStream(void) : m_bEnableCB(false), m_eEndianness(FEndian::ENDIAN_LITTLE), m_uiBytesRead(0), m_uiPosition(0) {}
FDataStream::~FDataStream(void) {}
unsigned int FDataStream::GetBytesUsed() const { return m_uiBytesRead; }
unsigned int FDataStream::GetPosition() const { return m_uiPosition; }
void FDataStream::SetPosition(unsigned int position) { m_uiPosition = position; }
void FDataStream::Truncate() {}
void FDataStream::Flush() {}
unsigned int FDataStream::GetEOF() const { return 0; }
unsigned int FDataStream::GetSizeLeft() const { return 0; }
void FDataStream::CopyToMem(void*) {}
void FDataStream::RaiseException(std::exception& kException) const { HHLogError("stream exception: %s", kException.what()); }

HHMemoryStream::HHMemoryStream() : m_uiPos(0), m_bOverrun(false) {}
HHMemoryStream::HHMemoryStream(const char* pData, size_t uiSize) : m_data(pData, pData + uiSize), m_uiPos(0), m_bOverrun(false) {}
HHMemoryStream::~HHMemoryStream() {}

unsigned int HHMemoryStream::ReadIt(unsigned int byteCount, void* buffer)
{
	const size_t n = std::min((size_t)byteCount, m_data.size() - m_uiPos);
	if(n)
		memcpy(buffer, &m_data[m_uiPos], n);
	if(n < byteCount)
	{
		memset((char*)buffer + n, 0, byteCount - n);
		m_bOverrun = true;
	}
	m_uiPos += n;
	m_uiPosition = (unsigned int)m_uiPos;
	m_uiBytesRead += (unsigned int)n;
	return (unsigned int)n;
}

unsigned int HHMemoryStream::WriteIt(unsigned int byteCount, const void* buffer)
{
	if(m_uiPos + byteCount > m_data.size())
		m_data.resize(m_uiPos + byteCount);
	if(byteCount)
		memcpy(&m_data[m_uiPos], buffer, byteCount);
	m_uiPos += byteCount;
	m_uiPosition = (unsigned int)m_uiPos;
	return byteCount;
}

void HHMemoryStream::Skip(unsigned int deltaByteCount) { SetPosition((unsigned int)(m_uiPos + deltaByteCount)); }
void HHMemoryStream::Rewind() { SetPosition(0); }
bool HHMemoryStream::AtEnd() const { return m_uiPos >= m_data.size(); }
void HHMemoryStream::FastFwd() { SetPosition((unsigned int)m_data.size()); }
unsigned int HHMemoryStream::GetBytesUsed() const { return (unsigned int)m_data.size(); }
unsigned int HHMemoryStream::GetPosition() const { return (unsigned int)m_uiPos; }
void HHMemoryStream::SetPosition(unsigned int position)
{
	m_uiPos = std::min((size_t)position, m_data.size());
	m_uiPosition = (unsigned int)m_uiPos;
}
void HHMemoryStream::Truncate() { m_data.resize(m_uiPos); }
void HHMemoryStream::Flush() {}
unsigned int HHMemoryStream::GetEOF() const { return (unsigned int)m_data.size(); }
unsigned int HHMemoryStream::GetSizeLeft() const { return (unsigned int)(m_data.size() - m_uiPos); }
void HHMemoryStream::CopyToMem(void* mem) { if(!m_data.empty()) memcpy(mem, &m_data[0], m_data.size()); }
void HHMemoryStream::RaiseException(std::exception& kException) const { HHLogError("memory stream exception: %s", kException.what()); }

namespace
{
	//--------------------------------------------------------------------------
	// The container's constants (every .Civ5Save of game build 403694)
	//--------------------------------------------------------------------------
	const unsigned int kSaveVersion = 8;
	const char* kGameVersion = "1.0.3.279 (403694)";
	const char* kGameBuild = "403694";
	const GUID kTailGuid = { 0x0d41152c, 0x948e, 0x498f, { 0x8c, 0xdf, 0x69, 0x9d, 0x04, 0xc9, 0x8a, 0x95 } };
	const unsigned int kBodyMarker = 2;
	const unsigned int kChunkBytes = 65536;

	//--------------------------------------------------------------------------
	// zlib1.dll of the game folder: the library and the calls the exe saves and loads with (deflateInit_ at the
	// default level, deflate, inflateInit_, inflate). Version 1.2.3; z_stream as zlib.h declares it.
	//--------------------------------------------------------------------------
	struct HHZStream
	{
		unsigned char* next_in;
		unsigned int avail_in;
		unsigned long total_in;
		unsigned char* next_out;
		unsigned int avail_out;
		unsigned long total_out;
		char* msg;
		void* state;
		void* zalloc;
		void* zfree;
		void* opaque;
		int data_type;
		unsigned long adler;
		unsigned long reserved;
	};
	typedef int (__cdecl *PFNDeflateInit)(HHZStream*, int, const char*, int);
	typedef int (__cdecl *PFNInflateInit)(HHZStream*, const char*, int);
	typedef int (__cdecl *PFNZCall)(HHZStream*, int);
	typedef int (__cdecl *PFNZEnd)(HHZStream*);
	const int Z_OK_ = 0, Z_STREAM_END_ = 1, Z_BUF_ERROR_ = -5, Z_NO_FLUSH_ = 0, Z_SYNC_FLUSH_ = 2, Z_DEFAULT_COMPRESSION_ = -1;

	struct HHZlib
	{
		bool bTried, bOk;
		PFNDeflateInit pfnDeflateInit;
		PFNZCall pfnDeflate;
		PFNZEnd pfnDeflateEnd;
		PFNInflateInit pfnInflateInit;
		PFNZCall pfnInflate;
		PFNZEnd pfnInflateEnd;
		HHZlib() : bTried(false), bOk(false) {}
	} s_zlib;

	bool LoadZlib(std::string& strError)
	{
		if(!s_zlib.bTried)
		{
			s_zlib.bTried = true;
			const std::string path = HHGetOptions().gameDir + "\\zlib1.dll";
			HMODULE h = LoadLibraryExW(HHWiden(path).c_str(), NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
			if(h)
			{
				s_zlib.pfnDeflateInit = (PFNDeflateInit)GetProcAddress(h, "deflateInit_");
				s_zlib.pfnDeflate = (PFNZCall)GetProcAddress(h, "deflate");
				s_zlib.pfnDeflateEnd = (PFNZEnd)GetProcAddress(h, "deflateEnd");
				s_zlib.pfnInflateInit = (PFNInflateInit)GetProcAddress(h, "inflateInit_");
				s_zlib.pfnInflate = (PFNZCall)GetProcAddress(h, "inflate");
				s_zlib.pfnInflateEnd = (PFNZEnd)GetProcAddress(h, "inflateEnd");
				s_zlib.bOk = s_zlib.pfnDeflateInit && s_zlib.pfnDeflate && s_zlib.pfnDeflateEnd && s_zlib.pfnInflateInit && s_zlib.pfnInflate &&
					s_zlib.pfnInflateEnd;
				HHLog("loaded %s%s", path.c_str(), s_zlib.bOk ? "" : " (a zlib call is missing)");
			}
			else
				HHLogError("cannot load %s (error %lu)", path.c_str(), GetLastError());
		}
		if(!s_zlib.bOk)
			strError = "zlib1.dll of the game folder is not usable";
		return s_zlib.bOk;
	}

	// All of raw with no flush, then one sync flush: the stream ends without a final block, as the exe's does.
	bool Deflate(const std::vector<char>& raw, std::vector<char>& out, std::string& strError)
	{
		if(!LoadZlib(strError))
			return false;
		HHZStream z;
		memset(&z, 0, sizeof(z));
		if(s_zlib.pfnDeflateInit(&z, Z_DEFAULT_COMPRESSION_, "1.2.3", (int)sizeof(z)) != Z_OK_)
		{
			strError = "deflateInit_ failed";
			return false;
		}
		std::vector<unsigned char> buf(kChunkBytes);
		z.next_in = raw.empty() ? NULL : (unsigned char*)&raw[0];
		z.avail_in = (unsigned int)raw.size();
		bool bOk = true;
		for(int flush = Z_NO_FLUSH_; bOk; flush = Z_SYNC_FLUSH_)
		{
			for(;;)
			{
				z.next_out = &buf[0];
				z.avail_out = (unsigned int)buf.size();
				const int ret = s_zlib.pfnDeflate(&z, flush);
				if(ret != Z_OK_ && ret != Z_BUF_ERROR_)
				{
					strError = HHFormat("deflate failed (%d)", ret);
					bOk = false;
					break;
				}
				out.insert(out.end(), buf.begin(), buf.begin() + (buf.size() - z.avail_out));
				if(z.avail_out != 0 && (flush == Z_SYNC_FLUSH_ || z.avail_in == 0))
					break;
			}
			if(flush == Z_SYNC_FLUSH_)
				break;
		}
		s_zlib.pfnDeflateEnd(&z);
		return bOk;
	}

	// Inflates until the input is used up (the stream has no end marker).
	bool Inflate(const std::vector<char>& in, std::vector<char>& raw, std::string& strError)
	{
		if(!LoadZlib(strError))
			return false;
		HHZStream z;
		memset(&z, 0, sizeof(z));
		if(s_zlib.pfnInflateInit(&z, "1.2.3", (int)sizeof(z)) != Z_OK_)
		{
			strError = "inflateInit_ failed";
			return false;
		}
		z.next_in = in.empty() ? NULL : (unsigned char*)&in[0];
		z.avail_in = (unsigned int)in.size();
		raw.resize(std::max((size_t)1 << 20, in.size() * 40));
		size_t used = 0;
		bool bOk = true;
		for(;;)
		{
			if(used == raw.size())
				raw.resize(raw.size() * 2);
			z.next_out = (unsigned char*)&raw[used];
			z.avail_out = (unsigned int)(raw.size() - used);
			const unsigned int before = z.avail_out;
			const int ret = s_zlib.pfnInflate(&z, Z_SYNC_FLUSH_);
			used += before - z.avail_out;
			if(ret == Z_STREAM_END_)
				break;
			if(ret == Z_BUF_ERROR_ && z.avail_in == 0)
				break;   // all input used: the exe's streams end on a sync flush
			if(ret != Z_OK_ && ret != Z_BUF_ERROR_)
			{
				strError = HHFormat("inflate failed (%d%s%s)", ret, z.msg ? ": " : "", z.msg ? z.msg : "");
				bOk = false;
				break;
			}
			if(z.avail_in == 0 && z.avail_out != 0)
				break;
		}
		s_zlib.pfnInflateEnd(&z);
		raw.resize(used);
		return bOk;
	}

	//--------------------------------------------------------------------------
	// Header bytes
	//--------------------------------------------------------------------------
	struct ByteWriter
	{
		std::vector<char> data;
		void Bytes(const void* p, size_t n) { data.insert(data.end(), (const char*)p, (const char*)p + n); }
		void U32(unsigned int v) { Bytes(&v, 4); }
		void U8(unsigned char v) { Bytes(&v, 1); }
		void Str(const std::string& s) { U32((unsigned int)s.size()); Bytes(s.data(), s.size()); }
		void Guid(const GUID& g) { Bytes(&g, sizeof(g)); }
	};

	struct ByteReader
	{
		const std::vector<char>& data;
		size_t pos;
		bool bOk;
		explicit ByteReader(const std::vector<char>& d, size_t p = 0) : data(d), pos(p), bOk(true) {}
		bool Has(size_t n) { if(pos + n > data.size()) bOk = false; return bOk; }
		unsigned int U32() { unsigned int v = 0; if(Has(4)) { memcpy(&v, &data[pos], 4); pos += 4; } return v; }
		unsigned char U8() { unsigned char v = 0; if(Has(1)) v = (unsigned char)data[pos++]; return v; }
		std::string Str()
		{
			const unsigned int n = U32();
			if(n > (1u << 20) || !Has(n))
			{
				bOk = false;
				return std::string();
			}
			std::string s(&data[pos], n);
			pos += n;
			return s;
		}
		GUID Guid() { GUID g; memset(&g, 0, sizeof(g)); if(Has(sizeof(g))) { memcpy(&g, &data[pos], sizeof(g)); pos += sizeof(g); } return g; }
	};

	bool ReadFileBytes(const std::string& strPath, std::vector<char>& out)
	{
		FILE* f = _wfopen(HHWiden(strPath).c_str(), L"rb");
		if(!f)
			return false;
		char buf[65536];
		size_t n;
		while((n = fread(buf, 1, sizeof(buf), f)) > 0)
			out.insert(out.end(), buf, buf + n);
		fclose(f);
		return true;
	}

	bool ParseHeader(const std::vector<char>& file, HHSaveHeader& h, std::string& strError)
	{
		ByteReader r(file);
		if(file.size() < 8 || memcmp(&file[0], "CIV5", 4) != 0)
		{
			strError = "not a .Civ5Save (no CIV5 at the start)";
			return false;
		}
		r.pos = 4;
		h.saveVersion = r.U32();
		h.gameVersion = r.Str();
		h.gameBuild = r.Str();
		h.turn = (int)r.U32();
		h.gameType = r.U8();
		h.civilization = r.Str();
		h.handicap = r.Str();
		h.startEra = r.Str();
		h.currentEra = r.Str();
		h.gameSpeed = r.Str();
		h.worldSize = r.Str();
		h.mapScript = r.Str();
		const unsigned int nDlc = r.U32();
		for(unsigned int i = 0; i < nDlc && r.bOk && i < 1000; ++i)
		{
			HHSaveDlc d;
			d.id = r.Guid();
			r.U32();
			d.name = r.Str();
			h.dlc.push_back(d);
		}
		const unsigned int nMods = r.U32();
		for(unsigned int i = 0; i < nMods && r.bOk && i < 1000; ++i)
		{
			HHSaveMod m;
			m.id = r.Str();
			m.version = (int)r.U32();
			m.name = r.Str();
			h.mods.push_back(m);
		}
		h.leaderName = r.Str();
		h.civilizationName = r.Str();
		h.playerColor = r.Str();
		h.dllGuid = r.Guid();
		h.dllVersion = r.Str();
		r.Guid();
		r.U32();
		h.pregameOffset = r.pos;
		if(!r.bOk)
		{
			strError = "the save's header is cut short or malformed";
			return false;
		}
		if(h.saveVersion != kSaveVersion)
		{
			strError = HHFormat("save version %u (this host reads %u)", h.saveVersion, kSaveVersion);
			return false;
		}
		return true;
	}

	bool ParseGuidText(std::string s, GUID& g)
	{
		s.erase(std::remove(s.begin(), s.end(), '{'), s.end());
		s.erase(std::remove(s.begin(), s.end(), '}'), s.end());
		unsigned int d1 = 0, d2 = 0, d3 = 0, b[8] = {0};
		if(sscanf(s.c_str(), "%8x-%4x-%4x-%2x%2x-%2x%2x%2x%2x%2x%2x", &d1, &d2, &d3, &b[0], &b[1], &b[2], &b[3], &b[4], &b[5], &b[6], &b[7]) != 11)
			return false;
		g.Data1 = d1;
		g.Data2 = (unsigned short)d2;
		g.Data3 = (unsigned short)d3;
		for(int i = 0; i < 8; ++i)
			g.Data4[i] = (unsigned char)b[i];
		return true;
	}

	std::string Between(const std::string& text, const std::string& open, const std::string& close, size_t from = 0)
	{
		const size_t a = text.find(open, from);
		if(a == std::string::npos)
			return std::string();
		const size_t b = text.find(close, a + open.size());
		return b == std::string::npos ? std::string() : text.substr(a + open.size(), b - a - open.size());
	}

	std::string Trim(const std::string& s)
	{
		const size_t a = s.find_first_not_of(" \t\r\n");
		if(a == std::string::npos)
			return std::string();
		return s.substr(a, s.find_last_not_of(" \t\r\n") - a + 1);
	}

	// The DLC a save lists: the keyed packages of Assets\DLC that are no upgrade package (the tablet data), in
	// folder-name order, without those an activated mod blocks (Vox Populi blocks DLC_SP_Maps), each with its en_US
	// name. Matches every save of this machine (12 without mods, 11 with Vox Populi).
	const std::vector<HHSaveDlc>& SaveDlcList()
	{
		static bool s_bDone = false;
		static std::vector<HHSaveDlc> s_list;
		if(s_bDone)
			return s_list;
		s_bDone = true;
		std::set<std::string> blocked;
		const HHOptions& o = HHGetOptions();
		const std::string modsDb = HHGetPaths().db + "Civ5ModsDatabase.db";
		if(!o.noMods && HHFileExists(modsDb))
		{
			Database::Connection db;
			if(db.Open(modsDb.c_str(), Database::OPEN_READONLY))
			{
				Database::Results r;
				if(db.Execute(r, "SELECT d.PackageId FROM DlcRelationships d JOIN Mods m ON m.ModID = d.ModID AND m.Version = d.Version "
				                 "WHERE m.Activated = 1 AND d.Type = -1"))
					while(r.Step())
						if(r.GetText(0))
						{
							GUID g = GUID();
							if(ParseGuidText(r.GetText(0), g))
								blocked.insert(HHGuidString(g));
						}
			}
		}
		std::vector<std::pair<std::string, HHSaveDlc> > found;
		const std::wstring dlcDir = HHWiden(o.gameDir + "\\Assets\\DLC\\");
		WIN32_FIND_DATAW fd;
		HANDLE hDir = FindFirstFileW((dlcDir + L"*").c_str(), &fd);
		if(hDir != INVALID_HANDLE_VALUE)
		{
			do
			{
				if(!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || fd.cFileName[0] == L'.')
					continue;
				const std::wstring folder = dlcDir + fd.cFileName + L"\\";
				WIN32_FIND_DATAW pd;
				HANDLE hPkg = FindFirstFileW((folder + L"*.Civ5Pkg").c_str(), &pd);
				if(hPkg == INVALID_HANDLE_VALUE)
					continue;
				do
				{
					std::string text;
					if(!HHReadTextFile(HHNarrow(folder + pd.cFileName), text))
						continue;
					GUID g = GUID();
					if(text.find("<Key>") == std::string::npos || text.find("<Upgrade>") != std::string::npos ||
					   !ParseGuidText(Trim(Between(text, "<GUID>", "</GUID>")), g) || blocked.count(HHGuidString(g)))
						continue;
					const std::string nameBlock = Between(text, "<Name>", "</Name>");
					const size_t v = nameBlock.find("<Value");
					HHSaveDlc d;
					d.id = g;
					d.name = Trim(v == std::string::npos ? nameBlock : Between(nameBlock, ">", "</Value>", v));
					// Sorted by folder, then file: \x01 sorts below every name character (Expansion before Expansion2).
					found.push_back(std::make_pair(HHLower(HHNarrow(fd.cFileName) + "\x01" + HHNarrow(pd.cFileName)), d));
				}
				while(FindNextFileW(hPkg, &pd));
				FindClose(hPkg);
			}
			while(FindNextFileW(hDir, &fd));
			FindClose(hDir);
		}
		struct ByFolder { bool operator()(const std::pair<std::string, HHSaveDlc>& a, const std::pair<std::string, HHSaveDlc>& b) const { return a.first < b.first; } };
		std::sort(found.begin(), found.end(), ByFolder());
		for(size_t i = 0; i < found.size(); ++i)
			s_list.push_back(found[i].second);
		HHLog("save DLC list: %u package(s), %u blocked by mods", (unsigned)s_list.size(), (unsigned)blocked.size());
		return s_list;
	}

	// The mods a save lists: the activated ones whose .modinfo says AffectsSavedGames (missing: yes), in activation
	// order. The real game lists only these (a VP save names Community Patch and Vox Populi, not the UI mods active
	// beside them), and loading a save whose list differs from the active mods makes it activate the save's list.
	std::vector<const HHActivatedMod*> SavedGameMods()
	{
		std::vector<const HHActivatedMod*> out;
		const std::vector<HHActivatedMod>& mods = HHGetVfs().GetMods();
		for(size_t i = 0; i < mods.size(); ++i)
		{
			bool bAffects = true;
			for(size_t k = 0; k < mods[i].properties.size(); ++k)
				if(mods[i].properties[k].first == "AffectsSavedGames")
					bAffects = Trim(mods[i].properties[k].second) != "0";
			if(bAffects)
				out.push_back(&mods[i]);
		}
		return out;
	}

	std::string TypeName(Database::Connection* pDb, const char* szTable, int id)
	{
		if(!pDb || id < 0)
			return std::string();
		Database::Results r;
		if(!pDb->Execute(r, HHFormat("SELECT Type FROM %s WHERE ID = ?", szTable).c_str()))
			return std::string();
		r.Bind(1, id);
		return r.Step() && r.GetText(0) ? std::string(r.GetText(0)) : std::string();
	}

	//--------------------------------------------------------------------------
	// Writing
	//--------------------------------------------------------------------------
	HHNewGameInterfaces* s_pSaveIf = NULL;
	std::vector<HHSaveRecord> s_saves;
	std::set<int> s_savedAtTurns;           // --save-at turns already written
	bool s_bLoaded = false;
	HHLoadRecord s_load;

	bool WriteSave(HHNewGameInterfaces& kIf, const std::string& strPath, HHSaveRecord& rec)
	{
		const double t0 = HHSeconds();
		ICvPreGame1* pg = kIf.pPreGame;
		const PlayerTypes eActive = pg->activePlayer();
		rec.path = strPath;
		rec.turn = kIf.pGame->GetGameTurn();
		rec.bytes = rec.bodyBytes = 0;

		// Header: the engine reads it from pregame, the active player and the database (trace hh-m0-s1-continents:
		// GetDLLGUID, GetDLLVersion, PreGame gameTurn, gameType, mapScriptName, era, gameSpeed, worldSize, then write).
		ByteWriter h;
		h.Bytes("CIV5", 4);
		h.U32(kSaveVersion);
		h.Str(kGameVersion);
		h.Str(kGameBuild);
		h.U32((unsigned int)pg->gameTurn());
		h.U8((unsigned char)pg->gameType());
		EraTypes eCurrentEra = pg->era();
		PlayerColorTypes eColor = NO_PLAYERCOLOR;
		if(ICvPlayer1* pPlayer = kIf.pGame->GetPlayer(eActive))
		{
			eCurrentEra = pPlayer->GetCurrentEra();
			eColor = pPlayer->GetPlayerColor();
			pPlayer->Destroy();
		}
		h.Str(TypeName(kIf.pDb, "Civilizations", pg->civilization(eActive)));
		h.Str(TypeName(kIf.pDb, "HandicapInfos", pg->handicap(eActive)));
		h.Str(TypeName(kIf.pDb, "Eras", pg->era()));
		h.Str(TypeName(kIf.pDb, "Eras", eCurrentEra));
		h.Str(TypeName(kIf.pDb, "GameSpeeds", pg->gameSpeed()));
		h.Str(TypeName(kIf.pDb, "Worlds", pg->worldSize()));
		h.Str(pg->mapScriptName().c_str());
		const std::vector<HHSaveDlc>& dlc = SaveDlcList();
		h.U32((unsigned int)dlc.size());
		for(size_t i = 0; i < dlc.size(); ++i)
		{
			h.Guid(dlc[i].id);
			h.U32(1);
			h.Str(dlc[i].name);
		}
		const std::vector<const HHActivatedMod*> mods = SavedGameMods();
		h.U32((unsigned int)mods.size());
		for(size_t i = 0; i < mods.size(); ++i)
		{
			h.Str(mods[i]->id);
			h.U32((unsigned int)mods[i]->version);
			h.Str(mods[i]->name);
		}
		h.Str(pg->leaderName(eActive).c_str());
		h.Str(pg->civilizationDescription(eActive).c_str());
		h.Str(TypeName(kIf.pDb, "PlayerColors", eColor));
		h.Guid(kIf.pContext->GetDLLGUID());
		h.Str(kIf.pContext->GetDLLVersion() ? kIf.pContext->GetDLLVersion() : "");
		h.Guid(kTailGuid);
		h.U32(0);

		HHMemoryStream kPregame;
		pg->write(kPregame);

		// The body, in the engine's order (trace: Game, Map, Team x 64, Player x 64, supporting class data).
		HHMemoryStream kBody;
		kIf.pGame->Write(kBody);
		kIf.pMap->Write(kBody);
		for(int i = 0; i < MAX_TEAMS; ++i)
			if(ICvTeam1* pTeam = kIf.pContext->GetTeam((TeamTypes)i))
			{
				pTeam->Write(kBody);
				pTeam->Destroy();
			}
		for(int i = 0; i < MAX_PLAYERS; ++i)
			if(ICvPlayer1* pPlayer = kIf.pGame->GetPlayer((PlayerTypes)i))
			{
				pPlayer->Write(kBody);
				pPlayer->Destroy();
			}
		kIf.pGame->WriteSupportingClassData(kBody);
		rec.bodyBytes = (unsigned int)kBody.Data().size();

		std::vector<char> compressed;
		if(!Deflate(kBody.Data(), compressed, rec.error))
			return false;
		ByteWriter file;
		file.Bytes(&h.data[0], h.data.size());
		if(!kPregame.Data().empty())
			file.Bytes(&kPregame.Data()[0], kPregame.Data().size());
		file.U32(kBodyMarker);
		for(size_t at = 0; at < compressed.size(); at += kChunkBytes)
		{
			const size_t n = std::min((size_t)kChunkBytes, compressed.size() - at);
			file.U32((unsigned int)n);
			file.Bytes(&compressed[at], n);
		}

		// Written next to the target and renamed, so a reader never sees half a save.
		const size_t slash = strPath.find_last_of('\\');
		if(slash != std::string::npos)
			HHEnsureDirectory(strPath.substr(0, slash + 1));
		const std::string tmp = strPath + ".tmp";
		FILE* f = _wfopen(HHWiden(tmp).c_str(), L"wb");
		const bool bWritten = f && fwrite(&file.data[0], 1, file.data.size(), f) == file.data.size();
		if(f)
			fclose(f);
		if(!bWritten || !MoveFileExW(HHWiden(tmp).c_str(), HHWiden(strPath).c_str(), MOVEFILE_REPLACE_EXISTING))
		{
			rec.error = HHFormat("cannot write %s (error %lu)", strPath.c_str(), GetLastError());
			DeleteFileW(HHWiden(tmp).c_str());
			return false;
		}
		rec.bytes = (unsigned int)file.data.size();
		rec.seconds = HHSeconds() - t0;
		return true;
	}

	// Every save stays in the run folder: <run>\saves (autosaves in saves\auto).
	std::string SaveDir()
	{
		std::string dir = HHGetPaths().run;
		if(!dir.empty() && dir[dir.size() - 1] != '\\')
			dir += '\\';
		return dir + "saves\\";
	}

	// name with the characters a file name cannot hold replaced by '-'.
	std::string FileNameSafe(std::string name)
	{
		for(size_t i = 0; i < name.size(); ++i)
			if(strchr("\\/:*?\"<>|", name[i]))
				name[i] = '-';
		return name;
	}

	// "<prefix>_<turn and date>.Civ5Save" as the game names its saves (ICvGame1::SetTimeStr: "0050 BC-2000").
	std::string SaveName(HHNewGameInterfaces& kIf, const std::string& strPrefix, int iTurn)
	{
		char szTime[256] = {0};
		kIf.pGame->SetTimeStr(szTime, iTurn, true);
		return FileNameSafe(strPrefix + "_" + (szTime[0] ? std::string(szTime) : HHFormat("%04d", iTurn)) + ".Civ5Save");
	}

	bool Save(const std::string& strKind, const std::string& strPath)
	{
		HHSaveRecord rec;
		rec.kind = strKind;
		const bool bOk = WriteSave(*s_pSaveIf, strPath, rec);
		if(bOk)
			HHLog("saved %s: %s (turn %d, %u bytes, body %u bytes, %.2f s)", strKind.c_str(), strPath.c_str(), rec.turn, rec.bytes, rec.bodyBytes, rec.seconds);
		else
			HHLogError("save %s failed: %s", strKind.c_str(), rec.error.c_str());
		s_saves.push_back(rec);
		return bOk;
	}
}

std::string HHGuidString(const GUID& g)
{
	return HHFormat("{%08x-%04x-%04x-%02x%02x-%02x%02x%02x%02x%02x%02x}", g.Data1, g.Data2, g.Data3, g.Data4[0], g.Data4[1], g.Data4[2],
	                g.Data4[3], g.Data4[4], g.Data4[5], g.Data4[6], g.Data4[7]);
}

bool HHReadSaveHeader(const std::string& strPath, HHSaveHeader& kHeader, std::string& strError)
{
	std::vector<char> file;
	if(!ReadFileBytes(strPath, file))
	{
		strError = "cannot read " + strPath;
		return false;
	}
	return ParseHeader(file, kHeader, strError);
}

void HHSetSaveGame(HHNewGameInterfaces* pIf)
{
	s_pSaveIf = pIf;
}

void HHOnAutoSave(bool bInitial, bool bPostTurn)
{
	if(!s_pSaveIf)
		return;
	const HHOptions& o = HHGetOptions();
	const int iTurn = s_pSaveIf->pGame->GetGameTurn();
	// --autosave: the game's rule (contract 6.5): the initial autosave of a new game, then every n-th turn's.
	if(o.autosaveTurns > 0 && (bInitial || iTurn % o.autosaveTurns == 0))
	{
		const std::string prefix = bInitial ? "AutoSave_Initial" : bPostTurn ? "AutoSave_Post" : "AutoSave";
		const std::string path = SaveDir() + "auto\\" + SaveName(*s_pSaveIf, prefix, iTurn);
		Save("autosave", path);
	}
	// --save-at T: the first autosave point of turn T that is not the initial one.
	if(!bInitial && std::find(o.saveAt.begin(), o.saveAt.end(), iTurn) != o.saveAt.end() && s_savedAtTurns.insert(iTurn).second)
		Save("turn", SaveDir() + SaveName(*s_pSaveIf, "Turn", iTurn));
}

void HHSaveFinal()
{
	if(s_pSaveIf)
		Save("final", SaveDir() + SaveName(*s_pSaveIf, "Final", s_pSaveIf->pGame->GetGameTurn()));
}

bool HHSaveNamed(const std::string& strName)
{
	if(!s_pSaveIf || strName.empty())
		return false;
	return Save("lua", SaveDir() + FileNameSafe(strName) + ".Civ5Save");
}

const std::vector<HHSaveRecord>& HHGetSaveRecords()
{
	return s_saves;
}

bool HHIsLoadedGame()
{
	return s_bLoaded;
}

const HHLoadRecord* HHGetLoadRecord()
{
	return s_load.path.empty() ? NULL : &s_load;
}

//------------------------------------------------------------------------------
// Loading
//------------------------------------------------------------------------------
bool HHLoadSavedGame(const std::string& strPath, HHNewGameInterfaces& kIf, HHNewGameResult& kResult)
{
	ICvPreGame1* pg = kIf.pPreGame;
	double t0 = HHSeconds();
	const double tStart = t0;
	s_load.path = strPath;
	std::vector<char> file;
	if(!ReadFileBytes(strPath, file))
	{
		kResult.error = "cannot read the save " + strPath;
		return false;
	}
	HHSaveHeader& h = s_load.header;
	if(!ParseHeader(file, h, kResult.error))
	{
		kResult.error = strPath + ": " + kResult.error;
		return false;
	}
	HHLog("save %s: turn %d, %s, %s, %s, %s, map %s, %u DLC, %u mod(s), DLL %s %s", strPath.c_str(), h.turn, h.civilization.c_str(),
	      h.handicap.c_str(), h.gameSpeed.c_str(), h.worldSize.c_str(), h.mapScript.c_str(), (unsigned)h.dlc.size(), (unsigned)h.mods.size(),
	      HHGuidString(h.dllGuid).c_str(), h.dllVersion.c_str());

	// The save must fit what the host booted: a single-player game of this DLL with these mods (the real game activates
	// the save's mods before loading it; c5h run --load passes them).
	if(h.gameType != GAME_SINGLE_PLAYER)
	{
		kResult.error = HHFormat("the save is a multiplayer game (game type %d); only single-player saves load", h.gameType);
		return false;
	}
	const GUID dll = kIf.pContext->GetDLLGUID();
	if(memcmp(&dll, &h.dllGuid, sizeof(GUID)) != 0)
	{
		kResult.error = "the save was written by game DLL " + HHGuidString(h.dllGuid) + " " + h.dllVersion + ", the loaded DLL is " + HHGuidString(dll) +
			" " + (kIf.pContext->GetDLLVersion() ? kIf.pContext->GetDLLVersion() : "");
		return false;
	}
	{
		// Every mod the save lists must be active, and every active mod that affects saved games must be listed (a
		// save lists only those; mods that do not, such as UI mods, may be active or not).
		const std::vector<HHActivatedMod>& mods = HHGetVfs().GetMods();
		const std::vector<const HHActivatedMod*> affecting = SavedGameMods();
		std::set<std::string> saved, active, needed;
		std::string savedList, neededList;
		for(size_t i = 0; i < h.mods.size(); ++i)
		{
			saved.insert(HHLower(h.mods[i].id) + HHFormat(":%d", h.mods[i].version));
			savedList += (i ? ", " : "") + h.mods[i].name + HHFormat(" v%d", h.mods[i].version);
		}
		for(size_t i = 0; i < mods.size(); ++i)
			active.insert(HHLower(mods[i].id) + HHFormat(":%d", mods[i].version));
		for(size_t i = 0; i < affecting.size(); ++i)
		{
			needed.insert(HHLower(affecting[i]->id) + HHFormat(":%d", affecting[i]->version));
			neededList += (i ? ", " : "") + affecting[i]->name + HHFormat(" v%d", affecting[i]->version);
		}
		if(!std::includes(active.begin(), active.end(), saved.begin(), saved.end()) ||
		   !std::includes(saved.begin(), saved.end(), needed.begin(), needed.end()))
		{
			kResult.error = "the save's mods (" + (savedList.empty() ? std::string("none") : savedList) + ") are not the activated mods that "
				"affect saved games (" + (neededList.empty() ? std::string("none") : neededList) + ")";
			return false;
		}
		size_t k = 0;  // the save's mods in the activation order
		for(size_t i = 0; i < mods.size() && k < h.mods.size(); ++i)
			if(HHLower(mods[i].id) == HHLower(h.mods[k].id))
				++k;
		if(k != h.mods.size())
			s_load.warnings.push_back("the mods are activated in another order than the save lists them");
	}
	if(h.gameVersion != kGameVersion)
		s_load.warnings.push_back("the save comes from game version " + h.gameVersion);
	for(size_t i = 0; i < s_load.warnings.size(); ++i)
		HHLog("warning: %s", s_load.warnings[i].c_str());

	// A. Pregame. ASSUMPTION (no trace of a load yet): the exe starts from a reset pregame, allows the save's DLC (no
	// pregame field holds them) and reads the save's pregame; the game type says it was loaded.
	HH_STEP("resetGame()", pg->resetGame());
	HH_STEP("resetPlayers()", pg->resetPlayers());
	HH_STEP("clearDLCAllowed()", pg->clearDLCAllowed());
	for(size_t i = 0; i < h.dlc.size(); ++i)
		pg->setDLCAllowed(h.dlc[i].id, true);
	kResult.steps.push_back(HHFormat("setDLCAllowed(..., true) x%u (the save's DLC)", (unsigned)h.dlc.size()));
	HHMemoryStream kPregame(&file[h.pregameOffset], file.size() - h.pregameOffset);
	HH_STEP("PreGame.read(stream, true)", pg->read(kPregame, true));
	HH_STEP(HHFormat("setGameType(%d, GAME_LOADED)", (int)pg->gameType()), pg->setGameType(pg->gameType(), GAME_LOADED));
	HH_STEP("setLoadFileName(save)", pg->setLoadFileName(CvString(strPath.c_str())));
	kResult.bPregameOk = true;

	// B. The body: u32 2, then the compressed chunks.
	ByteReader r(file, h.pregameOffset + kPregame.GetPosition());
	const unsigned int marker = r.U32();
	if(!r.bOk || marker != kBodyMarker || kPregame.Overrun())
	{
		kResult.error = HHFormat("the save's pregame did not end where the body starts (marker %u at offset %u); is this DLL the one that wrote it?",
		                         marker, (unsigned)(h.pregameOffset + kPregame.GetPosition()));
		return false;
	}
	std::vector<char> compressed;
	while(r.pos < file.size())
	{
		const unsigned int n = r.U32();
		if(!r.Has(n))
			break;
		compressed.insert(compressed.end(), file.begin() + r.pos, file.begin() + r.pos + n);
		r.pos += n;
	}
	if(!r.bOk)
	{
		kResult.error = "the save's compressed body is cut short";
		return false;
	}
	std::vector<char> raw;
	if(!Inflate(compressed, raw, kResult.error))
		return false;
	s_load.bodyBytes = (unsigned int)raw.size();
	kResult.timings.push_back(std::make_pair(std::string("load_read_file"), HHSeconds() - t0));

	// C. The game, read back in the order it was written. CvPlayer::gameStartInit (LaterInit) asks IsLoadedGame.
	t0 = HHSeconds();
	s_bLoaded = true;
	if(kIf.pDbUtil)
		HH_STEP("CacheGameDatabaseData()", kIf.pDbUtil->CacheGameDatabaseData());
	HHMemoryStream kBody(raw.empty() ? NULL : &raw[0], raw.size());
	HH_STEP("Game.Read(stream)", kIf.pGame->Read(kBody));
	HH_STEP("Map.Read(stream)", kIf.pMap->Read(kBody));
	for(int i = 0; i < MAX_TEAMS; ++i)
		if(ICvTeam1* pTeam = kIf.pContext->GetTeam((TeamTypes)i))
		{
			pTeam->Read(kBody);
			pTeam->Destroy();
		}
	kResult.steps.push_back(HHFormat("for i = 0..%d: GameContext.GetTeam(i).Read(stream)", MAX_TEAMS - 1));
	for(int i = 0; i < MAX_PLAYERS; ++i)
		if(ICvPlayer1* pPlayer = kIf.pGame->GetPlayer((PlayerTypes)i))
		{
			pPlayer->Read(kBody);
			pPlayer->Destroy();
		}
	kResult.steps.push_back(HHFormat("for i = 0..%d: Game.GetPlayer(i).Read(stream)", MAX_PLAYERS - 1));
	HH_STEP("Game.ReadSupportingClassData(stream)", kIf.pGame->ReadSupportingClassData(kBody));
	if(kBody.Overrun() || !kBody.AtEnd())
	{
		kResult.error = HHFormat("the DLL read %u of the save's %u body bytes%s; is this DLL the one that wrote it?", kBody.GetPosition(),
		                         (unsigned)raw.size(), kBody.Overrun() ? " and wanted more" : "");
		return false;
	}
	kResult.timings.push_back(std::make_pair(std::string("load_read_game"), HHSeconds() - t0));
	if(kIf.pGame->GetGameState() != GAMESTATE_ON)
	{
		// The stock DLL ends a game when its active player is dead and autoplay is over, which every autoplayed game's final
		// save shows. Such a game plays on, but no victory can end it any more.
		s_load.warnings.push_back("the save's game had ended, so it plays on in extended play, where no victory can end it; a --save-at save from before the last turn does not have this");
		HHLog("warning: %s", s_load.warnings.back().c_str());
	}

	// --reseed: new seeds for the loaded game. The map itself is in the save, so this changes only what is rolled from here
	// on. Vox Populi's stateless rolls (CvGame::randCore) hash the pregame's map seed; the game's generator (the DLL's
	// jonRand) takes the sync seed reduced as CvGame::init reduces it. The map generator cannot be reached and keeps the
	// save's state; after the map is made, the DLL hardly draws on it. The run's later saves carry the new seeds.
	const HHOptions& kOptions = HHGetOptions();
	if(kOptions.reseedMap >= 0)
		HH_STEP(HHFormat("setMapRandomSeed(%I64d)", kOptions.reseedMap), pg->setMapRandomSeed((unsigned int)kOptions.reseedMap));
	if(kOptions.reseedSync >= 0)
	{
		HH_STEP(HHFormat("setSyncRandomSeed(%I64d)", kOptions.reseedSync), pg->setSyncRandomSeed((unsigned int)kOptions.reseedSync));
		ICvRandom1* pRandom = kIf.pGame->GetRandomNumberGenerator();
		s_load.rngBefore = pRandom->GetSeed();
		HH_STEP(HHFormat("Game.GetRandomNumberGenerator().Reset(%I64d)", kOptions.reseedSync % 52319761), pRandom->Reset((unsigned long)(kOptions.reseedSync % 52319761)));
		s_load.rngAfter = pRandom->GetSeed();
		pRandom->Destroy();
		HHLog("reseed: the game generator's seed was %I64u, is %I64u", s_load.rngBefore, s_load.rngAfter);
	}
	s_load.bReseeded = kOptions.reseedMap >= 0 || kOptions.reseedSync >= 0;
	s_load.reseedMap = kOptions.reseedMap;
	s_load.reseedSync = kOptions.reseedSync;

	// D. The rest of the start as for a new game (setup/HHNewGame.h HHGameStartedSteps).
	t0 = HHSeconds();
	HHGameStartedSteps(kIf, kResult, true);
	// DLLs with the digest additions log per-turn digests from CvGame::init, which a load does not run.
	{
		const char* szChunk = "if Game and Game.SetHeadlessDigestLogging then Game.SetHeadlessDigestLogging(true) end";
		if(lua_State* L = kIf.pScripts->CreateLuaThread("Headless load"))
		{
			kIf.pScripts->RunString(L, szChunk, "=Headless load");
			kIf.pScripts->FreeLuaThread(L);
		}
		kResult.steps.push_back("Game.SetHeadlessDigestLogging(true) (DLLs with the digest additions)");
	}
	kResult.timings.push_back(std::make_pair(std::string("game_started"), HHSeconds() - t0));
	s_load.seconds = HHSeconds() - tStart;
	kResult.iWidth = kIf.pMap->GetGridWidth();
	kResult.iHeight = kIf.pMap->GetGridHeight();
	HHLog("game loaded: turn %d, map %dx%d, active player %d, %u body bytes in %.2f s", kIf.pGame->GetGameTurn(), kResult.iWidth,
	      kResult.iHeight, (int)pg->activePlayer(), s_load.bodyBytes, s_load.seconds);
	kResult.bStarted = true;
	return true;
}

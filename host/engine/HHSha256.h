// SHA-256 of files and buffers (FIPS 180-4), for the run report and the profile selection.
#pragma once
#include "HHCommon.h"

// Upper-case hex digest of a buffer.
std::string HHSha256(const void* pData, size_t uiBytes);
// Upper-case hex digest of a file's bytes; empty when the file cannot be read.
std::string HHSha256File(const std::string& strPath);
// What HHSha256File answers for this path from now on, without reading the file (--known-sha256).
void HHSha256Known(const std::string& strPath, const std::string& strHash);

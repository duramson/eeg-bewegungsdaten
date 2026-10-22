"""Row counts of the real files of 2007 to 2025. Skipped unless pytest runs with --realdata.

Needs `eeg-bewegungsdaten download` first. Raw line counts come from an independent count of the
lines in the ZIPs (header and lines without any value left out). The quarantine codes, MONAT_EMPTY
and the MaStR flags were checked against independent counts from the raw bytes, ENCODING_CP1252
against the number of lines that are not UTF-8; the 2025 codes were also checked by hand.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from eeg_bewegungsdaten.download import ArchivedFile, archived_files
from eeg_bewegungsdaten.ingest import ingest, latest_uploads
from eeg_bewegungsdaten.links import UENB, newest_known_year, older_links
from eeg_bewegungsdaten.paths import default_data_dir
from eeg_bewegungsdaten.reader import iter_chunks

# (year, TSO): sha256 of the ZIPs in part order, raw lines, quarantine codes, qa_flags
EXPECTED: dict[tuple[int, str], tuple[tuple[str, ...], int, dict[str, int], dict[str, int]]] = {
    (2007, "50hertz"): (
        ("3c6a4e745a19c6cafa6d77dcbc09137b884b57fb9fcf191b3e0d5011701973e3",),
        29_635,
        {},
        {"ENCODING_CP1252": 13742},
    ),
    (2007, "amprion"): (
        ("08a1af5d49fba3185774c7d9591d038e3efe74443327a4256de5129142766b60",),
        215_806,
        {},
        {"ENCODING_CP1252": 80},
    ),
    (2007, "tennet"): (
        ("dcee3ca548534714a2a0df9246ea8e30eae7ab0148d4bdda02164ed09aef93d1",),
        207_825,
        {},
        {"ENCODING_CP1252": 16077},
    ),
    (2007, "transnetbw"): (
        ("bfd3dd70dd6a8289f414ef8227213a66d4865c298cb5cf32bb404dc81c105154",),
        80_112,
        {},
        {"ENCODING_CP1252": 20},
    ),
    (2008, "50hertz"): (
        ("b522a6f7f884b26a7f13cd6d2b1b4288cefc2d5fc5577fcfe6a77f0edab48ee1",),
        39_274,
        {},
        {"ENCODING_CP1252": 18491},
    ),
    (2008, "amprion"): (
        ("8e1ae702d97d087daa1fc4b71db063c43d4002c536f454e0cf3a337913bab69c",),
        277_130,
        {},
        {"ENCODING_CP1252": 112},
    ),
    (2008, "tennet"): (
        ("244a8bb00b7f71f4d517283ecbaf239031d7fd24c31481af025dbe7729060c6a",),
        413_632,
        {},
        {"ENCODING_CP1252": 41958},
    ),
    (2008, "transnetbw"): (
        ("d49e337be012e4bc8014963ca45da4ce396031d89f7e8321a12f71036d2aa720",),
        106_611,
        {},
        {"ENCODING_CP1252": 22},
    ),
    (2009, "50hertz"): (
        ("9accab59a5962ced0b575d0cfc6a014df317f408b86521afb00c06b19f273ec4",),
        53_208,
        {},
        {"ENCODING_CP1252": 24882},
    ),
    (2009, "amprion"): (
        ("297505c8bd435a91f9a101c150d795f43cae59ade75da75df7226bd4ab4183b3",),
        366_027,
        {},
        {"ENCODING_CP1252": 222},
    ),
    (2009, "tennet"): (
        ("35b3a02e8be2f79ae1bb0a0b70e2ac744dd173f8ac3f6f46d80bfea2ea6d92e0",),
        587_609,
        {},
        {"ENCODING_CP1252": 68712},
    ),
    (2009, "transnetbw"): (
        ("1a81a4879dc587010937c3a4260fe9f08e55511379b107ff6478c038b081fe53",),
        136_448,
        {"BAD_NUMBER": 1},
        {"ENCODING_CP1252": 23},
    ),
    (2010, "amprion"): (
        ("28af6619e97ac5c278783a52a3575e8abdc889245e6d73af635cef8a803d7aaf",),
        567_402,
        {},
        {"ENCODING_CP1252": 424},
    ),
    (2010, "tennet"): (
        ("efbb2199c81d9564a53e397f0e7bea5ce97e6cb27f940158c3d6d2d5f99739aa",),
        892_674,
        {},
        {"ENCODING_CP1252": 98638},
    ),
    (2010, "transnetbw"): (
        ("9ffb71448d7f13f2d109d47930bf0dec3bd5ab129f596ed4b6103d12f65a862f",),
        184_239,
        {},
        {"ENCODING_CP1252": 24},
    ),
    (2010, "50hertz"): (
        ("c1a1037450a1b50bed03639fb0aa4e4c185710e60fcde52656c6c4b94c11917b",),
        71987,
        {},
        {"ENCODING_CP1252": 33563},
    ),
    (2011, "50hertz"): (
        ("a531528beb9921bdd1a1a6d8164c6c346456b776572db6190e0b9704ff652fdf",),
        248899,
        {},
        {"ENCODING_CP1252": 33420},
    ),
    (2011, "transnetbw"): (
        ("a43ee0a682b5411fa9492b2996a8aa49145da0ea5e4ca5109f6802ae05a7d867",),
        505464,
        {},
        {"ENCODING_CP1252": 86685},
    ),
    (2012, "50hertz"): (
        ("e3d5216ac07d3991d7d8a15c8d18114d9727125f8946866c0cc9aee8ef12aa37",),
        395701,
        {"BAD_NUMBER": 5},
        {"ENCODING_CP1252": 316},
    ),
    (2012, "amprion"): (
        ("b49271194abddac76d5e35d174ff411137605a31da660eac9b115521b7d570c6",),
        670858,
        {},
        {"ENCODING_CP1252": 283},
    ),
    (2012, "transnetbw"): (
        ("74696b4246033c417a873de45657846017a7a382c084bf9e659ebca07b98de8c",),
        620865,
        {},
        {"ENCODING_CP1252": 108799},
    ),
    (2013, "50hertz"): (
        ("92a68e1f00574d1341502aa40586e7f747be539b01a0f1399f095064cc6a4764",),
        513438,
        {"BAD_NUMBER": 31},
        {"ENCODING_CP1252": 660},
    ),
    (2013, "amprion"): (
        (
            "ce69bac72aaddf8a0fae43b9e223a8b69b37e88bf5237cada52949954a3fadcb",
            "7155e8e1723594653fc156e1711c8c4d409aeb63676d23874b82997fd2a44914",
        ),
        1254490,
        {},
        {"ENCODING_CP1252": 498},
    ),
    (2013, "transnetbw"): (
        ("a5016c4734064dbdbe504e2d8024b0429d8c304b10b8866b25586750decb7a2a",),
        684634,
        {},
        {"ENCODING_CP1252": 54548},
    ),
    (2014, "amprion"): (
        (
            "3d9685e67cee7b093298c640d777fe8c3026f3ab1d5b7303e5b2c8f005261b9d",
            "72174b96c732d5b49bddeea89efbf00cb14c44b5dc06a6f3233ac6f9ca2eabed",
        ),
        1301868,
        {},
        {"NO_PLANT_ID": 1},
    ),
    (2014, "transnetbw"): (
        ("35887d88f7388ceb13ece8e4ebbfe4b33da57d384300ad1872a10db973d63b7d",),
        732363,
        {},
        {"ENCODING_CP1252": 42057},
    ),
    (2015, "transnetbw"): (
        ("57168a6ecfbd3cde2be9620dd6a28053a16c690367110dac2c2bd74f989550f5",),
        753572,
        {},
        {"ENCODING_CP1252": 42681},
    ),
    (2011, "amprion"): (
        ("062ceb3a1c6d122d51aa4e113ddb2d74479aabc9e54d36cd6648d69730decbb7",),
        801_547,
        {"FIELD_COUNT": 8},
        {"ENCODING_CP1252": 276_415},
    ),
    (2011, "tennet"): (
        ("2d136855331435cc066b9387881b5aab00ac7b11ea8903e0d68ec7a592fbf9d4",),
        1_226_229,
        {},
        {"ENCODING_CP1252": 110_880},
    ),
    (2012, "tennet"): (
        ("edd993727c9fb808a43bca547961c3d364a3d5b638241f08a6436a07a782a183",),
        1_620_764,
        {},
        {"ENCODING_CP1252": 178_113},
    ),
    (2013, "tennet"): (
        ("5c304f8bba7c6587deb60910a2da8057e43b57335e9f47f3372691ba17fc0b55",),
        1_828_808,
        {},
        {"ENCODING_CP1252": 108_734},
    ),
    (2014, "50hertz"): (
        ("d99f184455a6eedbcad186d63ac3361120cf3ea5dd79de22c15dabd5c9cde3fa",),
        567_565,
        {},
        {},
    ),
    (2014, "tennet"): (
        ("defe974361c81c62788a79288866a9fc4d3680e78f34d335958fd3581aae0eba",),
        1_947_532,
        {},
        {},
    ),
    (2015, "50hertz"): (
        ("4ccfafa9c85c9b80d653a22e4a3e88aa6df19b3c7fe6756f212bfd870ab09561",),
        614_756,
        {"BAD_NUMBER": 1},
        {"ENCODING_CP1252": 74_162},
    ),
    (2015, "tennet"): (
        ("9cce08910cf6673d37a90adb046ec79db9da2dd6eac1cba690391b729fddac00",),
        2_022_732,
        {},
        {"ENCODING_CP1252": 238_075},
    ),
    (2016, "50hertz"): (
        ("b73a773dafa9373a87ccc1333f69790ed03d1d543b8eb51d19268ed786203450",),
        551_649,
        {},
        {"ENCODING_CP1252": 66_045},
    ),
    (2016, "amprion"): (
        ("5f2de88a3718f20d76b770e776b68996f95b5a9aeed2df640050864d02785bb7",),
        1_378_047,
        {},
        {"ENCODING_CP1252": 500},
    ),
    (2016, "tennet"): (
        ("e0b1b9874e54b6d052c064faa6c90c36bce9a1894dd90d3523f3b4b02050ef31",),
        2_062_201,
        {},
        {"ENCODING_CP1252": 252_843},
    ),
    (2016, "transnetbw"): (
        ("c9b6c39ba45c2ca71d63db12b5a393637b59de3ad888350cc36e33bb390a08ed",),
        779_341,
        {},
        {"ENCODING_CP1252": 112_374},
    ),
    (2017, "50hertz"): (
        ("6562c83f420e15b3bd87662d33dda10271ef22271caf7ecb977c24fdc6f3da55",),
        580_962,
        {},
        {"ENCODING_CP1252": 69_167},
    ),
    (2017, "amprion"): (
        ("f776472e6659cd885591ffbdb7e3377882d323d7dabb75412dc885f66611d431",),
        1_431_901,
        {},
        {"ENCODING_CP1252": 135_234},
    ),
    (2017, "tennet"): (
        ("be1671441801a60faaab13c8fc8846868c25b9142c0c15cc5d8d19c29cdd0d01",),
        2_131_567,
        {},
        {"ENCODING_CP1252": 258_827},
    ),
    (2017, "transnetbw"): (
        ("12dfea0d332738badf2f7fbf3973df64e2de3f63f89a86deb3e6018192f840fe",),
        807_715,
        {},
        {"ENCODING_CP1252": 116_266},
    ),
    (2018, "50hertz"): (
        ("dc0067401d96050d48535c06d047de8a5eeb7a7800122843188fea66ae31e526",),
        610_064,
        {},
        {"ENCODING_CP1252": 72_497},
    ),
    (2018, "amprion"): (
        ("9ad4012adbb3eacf38f7199f094d45f2ecd050bdff6f14d981ab5e861e2050fb",),
        1_396_775,
        {},
        {"ENCODING_CP1252": 357},
    ),
    (2018, "tennet"): (
        ("23c1efe1a18b9ab648d60c4e9ec9c2c2f7947b0459f8c5b5f7152919fd037395",),
        2_218_870,
        {},
        {"ENCODING_CP1252": 271_965},
    ),
    (2018, "transnetbw"): (
        ("7d406171e3e6f93abddf1907367d18c912103d3682cb50b88dea32ebbf4f1c46",),
        870_025,
        {},
        {"ENCODING_CP1252": 125_616},
    ),
    (2019, "50hertz"): (
        ("6ea41d34e3eefa6f16507c4d77697204da07abbfae796cb1ac24ecc53e45b885",),
        865_543,
        {},
        {"ENCODING_CP1252": 114_291},
    ),
    (2019, "amprion"): (
        ("d34d6211f8054c25c17d14029d5ae1e6cc6914b2e8b1bc557c57d0d0a9dfcfca",),
        1_464_286,
        {},
        {"ENCODING_CP1252": 359},
    ),
    (2019, "tennet"): (
        ("dc11f08de71dd26381231d56d62b6d6cace78faebf9b6a57a7993dc5b047db28",),
        2_331_902,
        {},
        {"ENCODING_CP1252": 146_318},
    ),
    (2019, "transnetbw"): (
        ("4b94c8a33cb28c3ca6dc8930c0da3c5010770c4edf1b442cef6088664dc05760",),
        911_459,
        {},
        {"ENCODING_CP1252": 156_431},
    ),
    (2020, "50hertz"): (
        ("5eae0ee92bd192b3143d518f2c85e11aca8aa33ca6b7382fbfbe3aec5a32db07",),
        728_044,
        {"UNDECODABLE": 8},
        {"ENCODING_CP1252": 354_235, "MASTR_NR_INVALID": 47, "MASTR_NR_MISSING": 128_712},
    ),
    (2020, "amprion"): (
        ("de36f46f56b6bfdad7dcf471e492173d721128771761812597cf7f30584ef2b9",),
        1_095_300,
        {},
        {
            "ENCODING_CP1252": 918_691,
            "MASTR_NR_INVALID": 435,
            "MASTR_NR_IS_UNIT": 179_492,
            "MASTR_NR_MISSING": 210_140,
            "MASTR_NR_MULTIPLE": 2_024,
            "NB_MASTR_NR_INVALID": 12,
        },
    ),
    (2020, "tennet"): (
        ("19dd74632f36e64de6e5b179865046d2cca4fa4f373d919adcb0007c0471365d",),
        2_283_220,
        {},
        {"ENCODING_CP1252": 1_355_391, "MASTR_NR_MISSING": 203_961},
    ),
    (2020, "transnetbw"): (
        ("5bd8a2e2edb79ec7c166601fb4ccc7db16aceb698590ff0f2272e5a804256886",),
        658_298,
        {},
        {
            "ENCODING_CP1252": 655_378,
            "MASTR_NR_INVALID": 7,
            "MASTR_NR_MISSING": 139_220,
            "NB_MASTR_NR_INVALID": 658_298,
        },
    ),
    (2021, "50hertz"): (
        ("b134b326062638b1abff3d7522ca54e45b78e9dfd6b0f5f81e1e06520b448f2a",),
        819_125,
        {},
        {
            "ENCODING_CP1252": 798_161,
            "MASTR_NR_INVALID": 68,
            "MASTR_NR_IS_UNIT": 1,
            "MASTR_NR_MISSING": 95_299,
        },
    ),
    (2021, "amprion"): (
        ("709efcbb19c8d18bb8f195256d89c8fe7298e1c0fe5674e89a56fdcf6424e217",),
        1_642_213,
        {},
        {
            "ENCODING_CP1252": 1_322_722,
            "MASTR_NR_INVALID": 787,
            "MASTR_NR_IS_UNIT": 50_694,
            "MASTR_NR_MISSING": 66_448,
            "MASTR_NR_MULTIPLE": 1_461,
            "MONAT_EMPTY": 1,
            "MONAT_INVALID": 3,
        },
    ),
    (2021, "tennet"): (
        ("7aad1ca45aa6271bc2d44b1c33788341091e0a89b43c490bb9de342583f0a840",),
        2_184_870,
        {},
        {"ENCODING_CP1252": 2_031_705, "MASTR_NR_MISSING": 75_127},
    ),
    (2021, "transnetbw"): (
        ("7ee9de0e8db734c604c28ccf3865a2344382c1c677c6c52d4b67dd91b6b1f45f",),
        880_281,
        {},
        {"ENCODING_CP1252": 846_616, "MASTR_NR_MISSING": 70_694},
    ),
    (2022, "50hertz"): (
        ("8e9d78eafe3ab4e9007a7ae959fcab3d7bd54d3adf67185c7b756cd261873ab6",),
        872_951,
        {},
        {"ENCODING_CP1252": 851_962, "MASTR_NR_MISSING": 39_428},
    ),
    (2022, "amprion"): (
        ("6256d028e3d86dde388e21ba8624e66d8273c1d589d82d3ffe6e38873719d662",),
        1_800_954,
        {},
        {
            "ENCODING_CP1252": 1_678_154,
            "MASTR_NR_INVALID": 1_088,
            "MASTR_NR_IS_UNIT": 2_223,
            "MASTR_NR_MISSING": 42_286,
            "MASTR_NR_MULTIPLE": 2_789,
        },
    ),
    (2022, "tennet"): (
        ("675e225a9e63ba3384b2361f8c6cd318605c1a25c12a50f39c080e5f13fcecf0",),
        2_562_077,
        {},
        {"ENCODING_CP1252": 2_398_376, "MASTR_NR_MISSING": 65_972},
    ),
    (2022, "transnetbw"): (
        ("b421ded0a918df33b5c5a579dce036342cf12c47dbbcead5e9967b17d71b0816",),
        1_027_094,
        {},
        {"ENCODING_CP1252": 987_147, "MASTR_NR_MISSING": 11_202},
    ),
    (2023, "50hertz"): (
        ("b227f97048eadbdf00384b967ff1a10109dbe8b8b835ec284a25bdb97304c078",),
        1_017_446,
        {},
        {"ENCODING_CP1252": 127_283, "MASTR_NR_MISSING": 9_892},
    ),
    (2023, "amprion"): (
        ("5f4d8cc585e015409295a79d58a43bdc97c31f50a674035ce1c41ae3017e5d10",),
        2_056_831,
        {},
        {
            "ENCODING_CP1252": 298,
            "MASTR_NR_INVALID": 1_192,
            "MASTR_NR_IS_UNIT": 17_920,
            "MASTR_NR_MISSING": 32_181,
            "MASTR_NR_MULTIPLE": 3_938,
            "MONAT_INVALID": 994,
        },
    ),
    (2023, "tennet"): (
        ("fedd4888227bd70307c42c48eba06d57b19a3027a4f8bc5cf7d3aac2877695e4",),
        2_853_278,
        {},
        {"ENCODING_CP1252": 218_037, "MASTR_NR_MISSING": 70_601},
    ),
    (2023, "transnetbw"): (
        ("91253ac5c77177bf728c46e041c83fe7bacb0798e0eae4eee831d46a5c593a4f",),
        1_169_258,
        {},
        {"ENCODING_CP1252": 185, "MASTR_NR_MISSING": 5_849},
    ),
    (2024, "50hertz"): (
        ("17ed5f4b8ee5ad25d7a9193aa439364b0091d4a8f88359b7576e863dac9232ac",),
        1_158_674,
        {},
        {"MASTR_NR_INVALID": 2, "MASTR_NR_MISSING": 7_616},
    ),
    (2024, "amprion"): (
        ("1c799d25d866680c04bce46b80ea86df5d72cb40664adc00fe88720dd1110bb2",),
        2_249_058,
        {"FIELD_COUNT": 2},
        {
            "ENCODING_CP1252": 210,
            "MASTR_NR_INVALID": 973,
            "MASTR_NR_IS_UNIT": 80_968,
            "MASTR_NR_MISSING": 37_157,
        },
    ),
    (2024, "tennet"): (
        (
            "3570cec5c8f00d9ed5527e70f06bccdf6568739916ded39e7ee59aaa27c21465",
            "d537e7250eda8ae61d4ce34a19adb7a5b2cfd02257eeea38d2f80c46c5446c1b",
            "23432f18928664380cb7ee7008c3cf773aad78a1ca94dbf8c37361e8db6e8837",
            "b9bc4162d78c0b88e26bbc7a08c482d0a791fb18b05fac3903af44cb752a1702",
        ),
        3_404_359,
        {},
        {"MASTR_NR_MISSING": 111_827, "MASTR_NR_MULTIPLE": 2_549, "NB_MASTR_NR_INVALID": 937},
    ),
    (2024, "transnetbw"): (
        ("82a819937591ed5947d8914072597c5c3d2fe91bd4c5ab767fac6eccce0c7537",),
        1_367_361,
        {},
        {
            "ENCODING_CP1252": 174,
            "EXTRA_VALUE": 3_622,
            "MASTR_NR_MISSING": 6_443,
            "MONAT_EMPTY": 921_281,
        },
    ),
    (2025, "50hertz"): (
        ("9fa4da2375e1eb2581708ca9e88f0ecfe840fb8edc965560fc695f53f935abdd",),
        1_400_862,
        {},
        {"MASTR_NR_INVALID": 2, "MASTR_NR_MISSING": 9_606},
    ),
    (2025, "amprion"): (
        ("124ddd279f69430b9fd10360caab7a65187bb01fe2ac6b8db92f27b6b853ff44",),
        2_626_635,
        {},
        {
            "ENCODING_CP1252": 199,
            "EXTRA_VALUE": 1,
            "MASTR_NR_INVALID": 4_483,
            "MASTR_NR_IS_UNIT": 185_245,
            "MASTR_NR_MISSING": 26_354,
            "MASTR_NR_MULTIPLE": 1,
        },
    ),
    (2025, "tennet"): (
        ("00f8324c5f44c1a45b890329fdc062d1aa7e4483895df6b8047342a56fa8724f",),
        3_791_801,
        {},
        {"MASTR_NR_MISSING": 89_829, "MASTR_NR_MULTIPLE": 2_666},
    ),
    (2025, "transnetbw"): (
        ("82540f1a3fc0cea75ca806824163d538048f4e383a63ed92e28b01b3779c49f6",),
        1_499_225,
        {},
        {"ENCODING_CP1252": 152, "MASTR_NR_MISSING": 6_135, "MONAT_EMPTY": 1_016_002},
    ),
}


# Uploads that a newer one replaced, kept for comparison; `ingest` reads the newer one. Same
# key, plus the publication date.
SUPERSEDED: dict[
    tuple[int, str, str], tuple[tuple[str, ...], int, dict[str, int], dict[str, int]]
] = {
    # Differs from the newer upload only in the row order and in 102,031 categories
    # "..K33b1eMPM<month>" that became "..K33b1-MPM<month>".
    (2020, "transnetbw", "2021-07-30"): (
        ("081d25e1eef537f5d8e4a47041bfd3edd860f3a36fc9fa3c2a07d4fb54a93217",),
        658_298,
        {},
        {
            "ENCODING_CP1252": 655_378,
            "MASTR_NR_INVALID": 7,
            "MASTR_NR_MISSING": 139_220,
            "NB_MASTR_NR_INVALID": 658_298,
        },
    ),
    # Without Monat: the newer upload has the same plants and sums, with 847,335 monthly rows.
    (2024, "tennet", "2025-09-16"): (
        (
            "958eb3895eab1828a54376e6d2a95b093ecceb75d579b16415c2e0097c8823e1",
            "eceb5f86ecf5c7f8f2808a06b17541ba3b90633dc45476af2fd6be41a3f506e9",
            "ec5321762e0f40cc5b154e9d2182fb8ec44a37add6b8527a2a7124e446baa60f",
        ),
        2_644_669,
        {},
        {"MASTR_NR_MISSING": 93_536, "MASTR_NR_MULTIPLE": 1_997, "NB_MASTR_NR_INVALID": 223},
    ),
}


def check_reference_counts(
    files: list[ArchivedFile],
    tmp_path: Path,
    raw: int,
    quarantine: dict[str, int],
    flags: dict[str, int],
) -> None:
    report = ingest(files, tmp_path)

    assert report["rows"]["raw"] == raw
    assert report["rows"]["clean"] + report["rows"]["quarantine"] == raw
    assert report["quarantine"] == quarantine
    assert report["qa_flags"] == flags
    text = pl.scan_parquet(report["outputs"]["text"])
    assert text.select(pl.len()).collect().item() == raw
    quarantined = pl.scan_parquet(report["outputs"]["quarantine"]).select("quelldatei", "zeile")
    cp1252 = (
        text.filter(pl.col("kodierung") == "cp1252")
        .join(quarantined, on=["quelldatei", "zeile"], how="anti")
        .select(pl.len())
        .collect()
        .item()
    )
    assert cp1252 == flags.get("ENCODING_CP1252", 0)


@pytest.mark.realdata
@pytest.mark.parametrize(("jahr", "uenb"), sorted(EXPECTED), ids=str)
def test_real_files_give_the_reference_counts(jahr: int, uenb: str, tmp_path: Path) -> None:
    sha256, raw, quarantine, flags = EXPECTED[(jahr, uenb)]
    uploads = [
        files
        for files in latest_uploads(default_data_dir())
        if (files[0].meta["year"], files[0].meta["uenb"]) == (jahr, uenb)
    ]
    if not uploads or tuple(f.meta["sha256"] for f in uploads[0]) != sha256:
        pytest.skip(f"{jahr} file of {uenb} not downloaded or changed upstream")

    check_reference_counts(uploads[0], tmp_path, raw, quarantine, flags)


@pytest.mark.realdata
@pytest.mark.parametrize(("jahr", "uenb", "published"), sorted(SUPERSEDED), ids=str)
def test_superseded_uploads_give_the_reference_counts(
    jahr: int, uenb: str, published: str, tmp_path: Path
) -> None:
    sha256, raw, quarantine, flags = SUPERSEDED[(jahr, uenb, published)]
    files = sorted(
        (f for f in archived_files(default_data_dir()) if f.meta["sha256"] in sha256),
        key=lambda f: sha256.index(f.meta["sha256"]),
    )
    if len(files) != len(sha256):
        pytest.skip(f"superseded {jahr} file of {uenb} not downloaded")
    assert all(f.meta["published"] == published for f in files)
    latest = {(u[0].meta["year"], u[0].meta["uenb"]): u for u in latest_uploads(default_data_dir())}
    assert latest[(jahr, uenb)][0].meta["published"] > published

    check_reference_counts(files, tmp_path, raw, quarantine, flags)


def test_every_file_before_2016_has_reference_counts() -> None:
    earlier = {(link.year, link.uenb) for link in older_links() if link.year and link.year < 2016}

    assert {key for key in EXPECTED if key[0] < 2016} == earlier


def test_every_year_and_tso_from_2016_has_reference_counts() -> None:
    assert [key for key in sorted(EXPECTED) if key[0] >= 2016] == [
        (jahr, uenb) for jahr in range(2016, newest_known_year() + 1) for uenb in UENB
    ]


# Counts made independently from the extracted bytes, excluding headers and blank lines.
ZENODO_ROWS: dict[str, int] = {
    "PaymentRegister/Transnet/Original files/2011.csv": 505_464,
    "PaymentRegister/Transnet/Original files/2012.csv": 620_865,
    "PaymentRegister/Transnet/Original files/2013.csv": 684_634,
    "PaymentRegister/Transnet/Original files/2014.csv": 732_363,
    "PaymentRegister/Transnet/Original files/2015.csv": 753_572,
    "PaymentRegister/Hertz/Original files/2010.csv": 71_987,
    "PaymentRegister/Hertz/Original files/2011.csv": 248_899,
    "PaymentRegister/Hertz/Original files/2012.csv": 395_701,
    "PaymentRegister/Hertz/Original files/2013.csv": 513_438,
    "PaymentRegister/Amprion/Original files/Bewegungsdaten_2012/2012_a.txt": 670_858,
    (
        "PaymentRegister/Amprion/Original files/Bewegungsdaten_2013/"
        "Internet_Bewegungsdaten_VNNE_2013.csv"
    ): 427_792,
    (
        "PaymentRegister/Amprion/Original files/Bewegungsdaten_2014/Bewegungsdaten_VGT_DV_2014.csv"
    ): 848_323,
    (
        "PaymentRegister/Amprion/Original files/Bewegungsdaten_2014/Bewegungsdaten_VNNE_2014.csv"
    ): 453_545,
    "PaymentRegister/Transnet/Original files/2007.csv": 80_112,
    "PaymentRegister/Transnet/Original files/2008.csv": 106_611,
    "PaymentRegister/Transnet/Original files/2009.csv": 136_448,
    "PaymentRegister/Transnet/Original files/2010.csv": 184_239,
    "PaymentRegister/Tennet/Original files/2007.csv": 207_825,
    "PaymentRegister/Tennet/Original files/2008.csv": 413_632,
    "PaymentRegister/Tennet/Original files/2009.csv": 587_609,
    "PaymentRegister/Tennet/Original files/2010.csv": 892_674,
    "PaymentRegister/Hertz/Original files/2007.csv": 29_635,
    "PaymentRegister/Hertz/Original files/2008.csv": 39_274,
    "PaymentRegister/Hertz/Original files/2009.csv": 53_208,
    "PaymentRegister/Amprion/Original files/Bewegungsdaten_2007/2007_a.txt": 215_806,
    "PaymentRegister/Amprion/Original files/Bewegungsdaten_2008/2008_a.txt": 277_130,
    "PaymentRegister/Amprion/Original files/Bewegungsdaten_2009/2009_a.txt": 366_027,
    "PaymentRegister/Amprion/Original files/Bewegungsdaten_2010/2010_a.txt": 567_402,
}


def test_every_zenodo_member_has_reference_counts() -> None:
    assert {link.zip_member for link in older_links() if link.zip_member} == set(ZENODO_ROWS)


@pytest.mark.realdata
@pytest.mark.parametrize("member", ZENODO_ROWS)
def test_each_zenodo_member_has_its_reference_row_count(member: str) -> None:
    link = next(link for link in older_links() if link.zip_member == member)
    files = [
        file
        for file in archived_files(default_data_dir())
        if file.meta.get("zip_member") == member and file.meta["sha256"] == link.sha256
    ]
    if not files:
        pytest.skip(f"{member} not downloaded or changed upstream")
    assert sum(chunk.frame.height for chunk in iter_chunks(files[0].path)) == ZENODO_ROWS[member]

const caseId = process.env.FRESH_CASE;
const group = process.env.FRESH_GROUP;

if (
  ![
    "TC-AT-TOKEN-401-001",
    "TC-AT-TOKEN-401-002",
    "TC-AT-OAUTH-CALLBACK-001",
    "TC-AT-OAUTH-CALLBACK-002",
  ].includes(String(caseId))
) {
  throw new Error("FRESH_CASE must select one frontend authentication case");
}
if (!["control", "experiment"].includes(String(group))) {
  throw new Error("FRESH_GROUP must be control or experiment");
}

let mockResponseInterceptor:
  | ((response: any, options: any) => Promise<any>)
  | undefined;
const mockRedirectToLogin = jest.fn();
const mockNavigate = jest.fn();
const mockSetSearchParams = jest.fn();
let mockSearchParams = new URLSearchParams();

jest.mock("umi-request", () => ({
  extend: jest.fn(() => ({
    interceptors: {
      request: { use: jest.fn() },
      response: {
        use: jest.fn((handler) => {
          mockResponseInterceptor = handler;
        }),
      },
    },
    get: jest.fn(),
    post: jest.fn(),
  })),
}));

jest.mock("@/components/ui/message", () => ({
  __esModule: true,
  default: { error: jest.fn() },
}));
jest.mock("@/locales/config", () => ({
  __esModule: true,
  default: { t: (key: string) => key },
}));
jest.mock("@/utils/notification", () => ({
  __esModule: true,
  default: { error: jest.fn() },
}));
jest.mock("@/utils/common-util", () => ({
  convertTheKeysOfTheObjectToSnake: (value: unknown) => value,
  getSearchValue: () => null,
  isFormData: () => false,
}));
jest.mock("@/utils/llm-cache", () => ({ setCachedLlmList: jest.fn() }));
jest.mock("@/utils/llm-util", () => ({
  addTenantParams: (value: unknown) => value,
}));
jest.mock("@/utils/authorization-util", () => {
  const actual = jest.requireActual("@/utils/authorization-util");
  return {
    __esModule: true,
    ...actual,
    redirectToLogin: mockRedirectToLogin,
  };
});
jest.mock("react-router", () => ({
  useNavigate: () => mockNavigate,
  useSearchParams: () => [mockSearchParams, mockSetSearchParams],
}));

require("@/utils/request");
const authorizationUtil = require("@/utils/authorization-util").default;
const { Authorization, Token, UserInfo } = require("@/constants/authorization");
const { useOAuthCallback } = require("@/hooks/auth-hooks");
const { renderHook, waitFor } = require("@testing-library/react");

function emit(observed: Record<string, unknown>) {
  process.stdout.write(
    `FRESH_OBSERVED=${JSON.stringify({ case_id: caseId, group, ...observed })}\n`,
  );
}

function fakeHttp401() {
  return {
    status: 401,
    clone: () => ({
      json: async () => ({ message: "expired" }),
    }),
  };
}

beforeEach(() => {
  localStorage.clear();
  mockRedirectToLogin.mockClear();
  mockNavigate.mockClear();
  mockSetSearchParams.mockClear();
  mockSearchParams = new URLSearchParams();
  jest.spyOn(console, "debug").mockImplementation(() => undefined);
});

afterEach(() => {
  jest.restoreAllMocks();
});

test(String(caseId), async () => {
  if (caseId === "TC-AT-TOKEN-401-001") {
    localStorage.setItem(Authorization, "expired-signed-state");
    localStorage.setItem(Token, "expired-secondary-state");
    localStorage.setItem(UserInfo, '{"fixture":true}');
    if (!mockResponseInterceptor)
      throw new Error("response interceptor missing");

    await mockResponseInterceptor(fakeHttp401(), {});
    const observed = {
      authorization_absent: localStorage.getItem(Authorization) === null,
      token_key_absent: localStorage.getItem(Token) === null,
      user_info_absent: localStorage.getItem(UserInfo) === null,
      redirect_calls: mockRedirectToLogin.mock.calls.length,
    };
    emit(observed);
    expect(observed).toEqual({
      authorization_absent: true,
      token_key_absent: true,
      user_info_absent: true,
      redirect_calls: 1,
    });
    return;
  }

  if (caseId === "TC-AT-TOKEN-401-002") {
    localStorage.setItem(Authorization, "expired-signed-state");
    localStorage.setItem(Token, "expired-secondary-state");
    localStorage.setItem(UserInfo, '{"fixture":true}');
    if (!mockResponseInterceptor)
      throw new Error("response interceptor missing");

    await Promise.all(
      Array.from({ length: 5 }, () =>
        mockResponseInterceptor!(fakeHttp401(), {}),
      ),
    );
    const observed = {
      authorization_absent: localStorage.getItem(Authorization) === null,
      token_key_absent: localStorage.getItem(Token) === null,
      user_info_absent: localStorage.getItem(UserInfo) === null,
      redirect_calls: mockRedirectToLogin.mock.calls.length,
      concurrent_response_count: 5,
    };
    emit(observed);
    expect(observed.redirect_calls).toBe(1);
    expect(observed.authorization_absent).toBe(true);
    expect(observed.token_key_absent).toBe(true);
    expect(observed.user_info_absent).toBe(true);
    return;
  }

  if (caseId === "TC-AT-OAUTH-CALLBACK-001") {
    const callbackValue = "fresh-callback-opaque-value";
    mockSearchParams = new URLSearchParams(`auth=${callbackValue}`);
    renderHook(() => useOAuthCallback());

    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith("/"));
    const updated = mockSetSearchParams.mock.calls[0]?.[0] as URLSearchParams;
    const stored = authorizationUtil.getAuthorization();
    const observed = {
      authorization_equals_callback: stored === callbackValue,
      bearer_prefix_absent: !String(stored).startsWith("Bearer "),
      user_info_absent: localStorage.getItem(UserInfo) === null,
      query_auth_removed: updated?.has("auth") === false,
      navigate_root_calls: mockNavigate.mock.calls.filter(
        ([path]: [string]) => path === "/",
      ).length,
      set_search_calls: mockSetSearchParams.mock.calls.length,
    };
    emit(observed);
    expect(observed).toEqual({
      authorization_equals_callback: true,
      bearer_prefix_absent: true,
      user_info_absent: true,
      query_auth_removed: true,
      navigate_root_calls: 1,
      set_search_calls: 1,
    });
    return;
  }

  renderHook(() => useOAuthCallback());
  await new Promise((resolve) => setTimeout(resolve, 0));
  const observed = {
    authorization_absent: authorizationUtil.getAuthorization() === null,
    set_search_calls: mockSetSearchParams.mock.calls.length,
    navigate_calls: mockNavigate.mock.calls.length,
  };
  emit(observed);
  expect(observed).toEqual({
    authorization_absent: true,
    set_search_calls: 0,
    navigate_calls: 0,
  });
});

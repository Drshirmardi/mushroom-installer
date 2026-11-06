%% Consultation-only admission, publication and delivery policy for ejabberd 26.09.
%% Deliberately has no XML/body/token logging. API failure always drops traffic.
-module(mod_sabz_chat).
-behaviour(gen_mod).
-export([start/2, stop/1, reload/3, depends/2, mod_options/1, mod_opt_type/1,
         outgoing/1, incoming/1, check_jwt/5]).
-record(xmlel, {name, attrs = [], children = []}).
-define(NS, <<"urn:sabz:chat:1">>).

start(Host, _Opts) ->
    {ok, _} = application:ensure_all_started(inets),
    _ = secret(Host),
    ejabberd_hooks:add(user_send_packet, Host, ?MODULE, outgoing, 10),
    ejabberd_hooks:add(user_receive_packet, Host, ?MODULE, incoming, 10),
    ejabberd_hooks:add(check_decoded_jwt, Host, ?MODULE, check_jwt, 200),
    ok.
stop(Host) ->
    ejabberd_hooks:delete(user_send_packet, Host, ?MODULE, outgoing, 10),
    ejabberd_hooks:delete(user_receive_packet, Host, ?MODULE, incoming, 10),
    ejabberd_hooks:delete(check_decoded_jwt, Host, ?MODULE, check_jwt, 200),
    ok.
reload(_Host, _New, _Old) -> ok.
depends(_Host, _Opts) -> [{mod_muc, hard}, {mod_mam, hard}].
mod_options(_Host) -> [{policy_url, <<"http://api:3003/v1/chat/ejabberd/policy">>}, {timeout_ms, 2500}].
mod_opt_type(policy_url) -> econf:binary();
mod_opt_type(timeout_ms) -> econf:pos_int().

%% Native JWT verification (signature, jid and exp) runs before this hook.
check_jwt(false, _Fields, _Signature, _Host, _User) -> false;
check_jwt(true, _Fields, _Signature, _Host, <<"bridge">>) -> true;
check_jwt(true, Fields, _Signature, Host, _User) ->
    try
        Jid = maps:get(<<"jid">>, Fields),
        allowed(policy(Host, <<"SESSION">>, <<Jid/binary, "/sabz">>, #{}))
    catch _:_ -> false end.

outgoing({drop, State}) -> {drop, State};
outgoing({Packet, State}) ->
    try
        Host = maps:get(lserver, State), Actor = jid:encode(maps:get(jid, State)),
        Xml = xmpp:encode(Packet),
        Result = send_xml(Host, Actor, Xml),
        {xmpp:set_meta(xmpp:decode(Result), xmpp:get_meta(Packet)), State}
    catch
        throw:policy_denied -> reject(Packet, State, xmpp:err_forbidden());
        throw:policy_rate_limited -> reject(Packet, State, xmpp:err_resource_constraint());
        error:forbidden -> reject(Packet, State, xmpp:err_forbidden());
        _:_ -> reject(Packet, State, xmpp:err_service_unavailable())
    end.

reject(Packet, State, Error) ->
    %% Incoming filtering strips the original text from error reflections.
    try ejabberd_router:route(xmpp:make_error(Packet, Error)) catch _:_ -> ok end,
    {drop, State}.

incoming({drop, State}) -> {drop, State};
incoming({Packet, State}) ->
    try
        Host = maps:get(lserver, State), Actor = jid:encode(maps:get(jid, State)),
        true = allowed(policy(Host, <<"SESSION">>, Actor, #{})),
        {xmpp:set_meta(xmpp:decode(receive_xml(Host, Actor, xmpp:encode(Packet), 0)), xmpp:get_meta(Packet)), State}
    catch _:_ -> {drop, State} end.

send_xml(Host, Actor, Xml = #xmlel{name = <<"message">>}) ->
    Room = room(attr(<<"to">>, Xml), Host),
    <<"groupchat">> = attr(<<"type">>, Xml),
    case children(<<"body">>, Xml) of
        [Body] ->
            ClientId = attr(<<"id">>, Xml), true = valid_uuid(ClientId),
            Data = policy(Host, <<"SEND">>, Actor, #{roomJid => Room,
                clientMessageId => ClientId, body => text(Body)}),
            true = allowed(Data),
            safe_message(Xml, Data);
        [] ->
            case receipt(Xml) of
                {Action, Id} ->
                    true = valid_uuid(Id),
                    true = allowed(policy(Host, Action, Actor, #{roomJid => Room, messageId => Id})),
                    Xml#xmlel{children = [receipt_element(Xml)]};
                none ->
                    true = typing_only(Xml),
                    true = allowed(policy(Host, <<"JOIN">>, Actor, #{roomJid => Room})),
                    Xml
            end
    end;
send_xml(Host, Actor, Xml = #xmlel{name = <<"presence">>}) ->
    case attr(<<"to">>, Xml) of
        <<>> -> true = allowed(policy(Host, <<"SESSION">>, Actor, #{})), Xml#xmlel{children = []};
        To ->
            Room = room(To, Host),
            Action = case attr(<<"type">>, Xml) of <<"unavailable">> -> <<"HISTORY">>; <<>> -> <<"JOIN">>; _ -> error(forbidden) end,
            true = allowed(policy(Host, Action, Actor, #{roomJid => Room})),
            %% No client-controlled invitation, role change or historic join replay.
            History = #xmlel{name = <<"history">>, attrs = [{<<"maxstanzas">>, <<"0">>}]},
            Xml#xmlel{children = [#xmlel{name = <<"x">>, attrs = [{<<"xmlns">>, <<"http://jabber.org/protocol/muc">>}], children = [History]}]}
    end;
send_xml(Host, Actor, Xml = #xmlel{name = <<"iq">>}) ->
    To = bare(attr(<<"to">>, Xml)),
    case To =:= Host orelse To =:= <<>> of
        true ->
            true = server_iq(Xml),
            true = allowed(policy(Host, <<"SESSION">>, Actor, #{})), Xml;
        false ->
            Room = room(To, Host),
            true = history_iq(Xml),
            true = allowed(policy(Host, <<"HISTORY">>, Actor, #{roomJid => Room})), Xml
    end;
send_xml(_Host, _Actor, _Xml) -> error(forbidden).

%% Walk MAM's forwarded envelope too. Its archived body is never sent directly:
%% fetch the current canonical visibility/body from the application ledger.
receive_xml(_Host, _Actor, _Xml, Depth) when Depth > 10 -> error(forbidden);
receive_xml(Host, Actor, Xml = #xmlel{name = <<"message">>}, Depth) ->
    case attr(<<"type">>, Xml) of
        <<"error">> -> Xml#xmlel{children = children(<<"error">>, Xml)};
        _ ->
            case children(<<"body">>, Xml) of
                [] ->
                    From = bare(attr(<<"from">>, Xml)),
                    case From =:= Host orelse From =:= <<>> of
                        true -> ok;
                        false ->
                            Room = room(From, Host),
                            true = allowed(policy(Host, <<"HISTORY">>, Actor, #{roomJid => Room}))
                    end,
                    descend(Host, Actor, Xml, Depth);
                [_] ->
                    Room = room(attr(<<"from">>, Xml), Host),
                    [Marker] = [E || E <- children(<<"sabz">>, Xml), attr(<<"xmlns">>, E) =:= ?NS],
                    Id = attr(<<"id">>, Marker), true = valid_uuid(Id),
                    Data = policy(Host, <<"DELIVER">>, Actor, #{roomJid => Room, messageId => Id}),
                    true = allowed(Data), safe_message(Xml, Data)
            end
    end;
receive_xml(Host, Actor, Xml = #xmlel{name = <<"presence">>}, _Depth) ->
    Room = room(attr(<<"from">>, Xml), Host),
    true = allowed(policy(Host, <<"HISTORY">>, Actor, #{roomJid => Room})), Xml;
receive_xml(Host, Actor, Xml, Depth) -> descend(Host, Actor, Xml, Depth).

descend(Host, Actor, Xml, Depth) ->
    Xml#xmlel{children = [case Child of
        #xmlel{} -> receive_xml(Host, Actor, Child, Depth + 1);
        _ -> Child
    end || Child <- Xml#xmlel.children]}.

safe_message(Xml, Data) ->
    Id = maps:get(<<"messageId">>, Data),
    Body = #xmlel{name = <<"body">>, children = [{xmlcdata, maps:get(<<"body">>, Data)}]},
    Marker = #xmlel{name = <<"sabz">>, attrs = [
        {<<"xmlns">>, ?NS}, {<<"id">>, Id},
        {<<"sequence">>, maps:get(<<"sequence">>, Data)},
        {<<"sender-kind">>, maps:get(<<"senderKind">>, Data)},
        {<<"created-at">>, maps:get(<<"createdAt">>, Data)},
        {<<"visibility">>, maps:get(<<"visibility">>, Data)}]},
    Origin = #xmlel{name = <<"origin-id">>, attrs = [{<<"xmlns">>, <<"urn:xmpp:sid:0">>}, {<<"id">>, maps:get(<<"clientMessageId">>, Data)}]},
    Xml#xmlel{attrs = lists:keystore(<<"id">>, 1, Xml#xmlel.attrs, {<<"id">>, Id}), children = [Body, Marker, Origin]}.

server_iq(Xml) ->
    Type = attr(<<"type">>, Xml),
    Type =:= <<"result">> orelse Type =:= <<"error">> orelse
        single_namespace(Xml, [<<"urn:xmpp:ping">>, <<"http://jabber.org/protocol/disco#info">>, <<"urn:ietf:params:xml:ns:xmpp-session">>]).
history_iq(Xml) ->
    Type = attr(<<"type">>, Xml),
    (Type =:= <<"get">> orelse Type =:= <<"set">>) andalso
        single_namespace(Xml, [<<"urn:xmpp:mam:2">>, <<"http://jabber.org/protocol/disco#info">>]).
single_namespace(Xml, Namespaces) ->
    case [E || E = #xmlel{} <- Xml#xmlel.children] of
        [E] -> lists:member(attr(<<"xmlns">>, E), Namespaces);
        _ -> false
    end.
typing_only(Xml) -> single_namespace(Xml, [<<"http://jabber.org/protocol/chatstates">>]).
receipt(Xml) ->
    case receipt_elements(Xml) of
        [E = #xmlel{name = <<"displayed">>}] -> {<<"READ">>, attr(<<"id">>, E)};
        [E = #xmlel{name = <<"received">>}] -> {<<"RECEIPT">>, attr(<<"id">>, E)};
        _ -> none
    end.
receipt_element(Xml) -> [E] = receipt_elements(Xml), E.
receipt_elements(Xml) -> [E || E = #xmlel{name = Name} <- Xml#xmlel.children,
    (Name =:= <<"received">> orelse Name =:= <<"displayed">>),
    lists:member(attr(<<"xmlns">>, E), [<<"urn:xmpp:receipts">>, <<"urn:xmpp:chat-markers:0">>])].

room(To, Host) ->
    Bare = bare(To),
    [Name, Domain] = binary:split(Bare, <<"@">>, [global]),
    <<"conference.", Host/binary>> = Domain,
    {match, _} = re:run(Name, <<"^c[0-9a-f]{32}$">>), Bare.
bare(Jid) -> hd(binary:split(Jid, <<"/">>)).
attr(Key, #xmlel{attrs = Attrs}) -> proplists:get_value(Key, Attrs, <<>>).
children(Name, #xmlel{children = Children}) -> [E || E = #xmlel{name = N} <- Children, N =:= Name].
text(#xmlel{children = Children}) ->
    true = lists:all(fun({xmlcdata, _}) -> true; (_) -> false end, Children),
    iolist_to_binary([B || {xmlcdata, B} <- Children]).
valid_uuid(Value) -> re:run(Value, <<"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-4[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$">>, [{capture, none}]) =:= match.
allowed(Data) -> maps:get(<<"allow">>, Data, false) =:= true.

secret(Host) ->
    _ = Host,
    Secret = os:getenv("EJABBERD_POLICY_SECRET"),
    true = is_list(Secret) andalso length(Secret) >= 43, Secret.
policy(Host, Action, Actor, Extra) ->
    Url = binary_to_list(gen_mod:get_module_opt(Host, ?MODULE, policy_url)),
    Timeout = gen_mod:get_module_opt(Host, ?MODULE, timeout_ms),
    Body = misc:json_encode(maps:merge(#{action => Action, actorJid => Actor}, Extra)),
    Headers = [{"x-chat-policy-key", secret(Host)}],
    Result = httpc:request(post,
        {Url, Headers, "application/json", Body},
        [{timeout, Timeout}, {connect_timeout, Timeout}, {autoredirect, false}], [{body_format, binary}]),
    case Result of
        {ok, {{_, 200, _}, _, Response}} ->
            true = byte_size(Response) =< 65536,
            misc:json_decode(Response);
        {ok, {{_, Status, _}, _, _}} when Status =:= 401; Status =:= 403 -> throw(policy_denied);
        {ok, {{_, 429, _}, _, _}} -> throw(policy_rate_limited);
        _ -> error(policy_unavailable)
    end.
